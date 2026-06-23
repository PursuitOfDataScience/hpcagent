"""Mirror an online documentation site into a local markdown cache.

Given a URL to a docs/user-guide site, discover its pages (sitemap first, then a
breadth-first crawl of same-site links), extract each to markdown, and write them
to a local directory so the agent can read them instantly and offline. The source
URL is recorded in a manifest so the mirror can be re-synced later.

The crawl is bounded by both a page cap and a wall-clock budget so it never hangs
on large or slow sites.
"""

import gzip
import json
import os
import re
import time
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

DOCS_CACHE_ROOT = os.path.expanduser("~/.cache/hpcagent/docs")

# Non-document assets we never want to fetch/extract.
_SKIP_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".css", ".js", ".map", ".pdf", ".zip", ".gz", ".tar", ".tgz",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".mov", ".webm",
    ".mp3", ".wav", ".json", ".xml", ".csv", ".txt", ".rss", ".atom",
)


def _load_trafilatura():
    try:
        import trafilatura
        return trafilatura
    except ImportError:
        return None


def is_url(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return value.strip().lower().startswith(("http://", "https://"))


def slugify(value: str, maxlen: int = 80) -> str:
    value = re.sub(r"^https?://", "", value or "")
    value = re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("_")
    return (value or "page")[:maxlen]


def mirror_dir_for(url: str) -> str:
    """Stable local directory for a given source URL."""
    parsed = urlparse(url)
    slug = slugify(parsed.netloc + parsed.path)
    return os.path.join(DOCS_CACHE_ROOT, slug)


def _session():
    import requests
    s = requests.Session()
    s.headers.update({"User-Agent": "hpcagent-docs/0.1 (+local docs mirror)"})
    return s


def _norm_url(u: str) -> str:
    p = urlparse(u)
    path = p.path
    if path == "/":
        path = ""
    elif len(path) > 1 and path.endswith("/"):
        path = path.rstrip("/")
    return p._replace(path=path, fragment="", query="").geturl()


def _path_prefix(parsed) -> str:
    """Directory prefix of a URL path (so /a/b stays under /a/)."""
    prefix = parsed.path
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"
    return prefix


def _fetch_html(session, url: str, timeout: int = 12) -> str | None:
    try:
        r = session.get(url, timeout=timeout)
    except Exception:
        return None
    ctype = r.headers.get("content-type", "").lower()
    if r.status_code == 200 and ("html" in ctype or not ctype):
        return r.text
    return None


def _extract_links(html: str, base_url: str, host: str, prefix: str) -> list[str]:
    links = []
    for href in re.findall(r'href=["\']([^"\']+)["\']', html):
        if href.startswith(("mailto:", "tel:", "javascript:", "data:")):
            continue
        absu = urljoin(base_url, href)
        p = urlparse(absu)
        if p.scheme not in ("http", "https") or p.netloc != host:
            continue
        if p.path.lower().endswith(_SKIP_EXT):
            continue
        if prefix and len(prefix) > 1 and not p.path.startswith(prefix):
            continue
        links.append(_norm_url(absu))
    return links


def _sitemap_urls(start: str, host: str, timeout: int = 8) -> list[str]:
    """Best-effort, fast sitemap read (handles .gz and sitemap-index)."""
    urls: list[str] = []
    try:
        session = _session()
        for path in ("/sitemap.xml", "/sitemap.xml.gz"):
            try:
                r = session.get(urljoin(start, path), timeout=timeout)
            except Exception:
                continue
            if r.status_code != 200:
                continue
            try:
                raw = gzip.decompress(r.content) if path.endswith(".gz") else r.content
                text = raw.decode("utf-8", "replace")
            except Exception:
                continue
            locs = re.findall(r"<loc>\s*(.*?)\s*</loc>", text)
            for loc in locs:
                if loc.endswith((".xml", ".xml.gz")):  # nested sitemap index
                    try:
                        rr = session.get(loc, timeout=timeout)
                        sub = gzip.decompress(rr.content) if loc.endswith(".gz") else rr.content
                        urls += re.findall(r"<loc>\s*(.*?)\s*</loc>", sub.decode("utf-8", "replace"))
                    except Exception:
                        pass
                else:
                    urls.append(loc)
            if urls:
                break
    except Exception:
        return []
    return [_norm_url(u) for u in urls if urlparse(u).netloc == host]


def _extract_markdown(traf, html: str) -> str:
    for kwargs in ({"output_format": "markdown", "include_tables": True},
                   {"include_tables": True}):
        try:
            text = traf.extract(html, **kwargs)
            if text:
                return text
        except TypeError:
            continue
        except Exception:
            return ""
    return ""


def _page_title(traf, html: str) -> str:
    try:
        meta = traf.extract_metadata(html)
        if meta and getattr(meta, "title", None):
            return meta.title
    except Exception:
        pass
    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip() if m else ""


def mirror_docs(url: str, dest_dir: str | None = None, max_pages: int = 100,
                progress=None, time_budget: int = 180) -> dict:
    """Crawl ``url`` into a local markdown mirror. Returns a manifest dict.

    Single integrated pass: each page is fetched once, extracted to markdown, and
    its same-site links are queued. Seeded by the sitemap when available, then a
    breadth-first crawl of links under the URL's path. Bounded by ``max_pages``
    and ``time_budget`` (seconds).

    ``progress`` is an optional callable ``(saved, cap, url)`` for UI updates.
    Raises RuntimeError if trafilatura is missing or no pages can be extracted.
    """
    traf = _load_trafilatura()
    if traf is None:
        raise RuntimeError(
            "The 'trafilatura' package is required to mirror docs from a URL. "
            "Install with: pip install trafilatura  (or: pip install 'hpcagent[full]')"
        )

    dest_dir = dest_dir or mirror_dir_for(url)
    start = _norm_url(url)
    parsed = urlparse(start)
    host = parsed.netloc
    prefix = _path_prefix(parsed)

    session = _session()

    # Seed queue: start page first, then sitemap entries (filtered to the prefix).
    queue: list[str] = [start]
    seeds = _sitemap_urls(start, host)
    if prefix and len(prefix) > 1:
        under = [u for u in seeds if urlparse(u).path.startswith(prefix)]
        if len(under) >= 3:
            seeds = under
    queue.extend(seeds)

    os.makedirs(dest_dir, exist_ok=True)
    for fn in os.listdir(dest_dir):
        if fn.endswith(".md"):
            try:
                os.remove(os.path.join(dest_dir, fn))
            except OSError:
                pass

    visited: set[str] = set()
    queued: set[str] = set(queue)
    pages: list[dict[str, str]] = []
    used: set[str] = set()
    deadline = time.time() + time_budget

    while queue and len(pages) < max_pages and time.time() < deadline:
        u = queue.pop(0)
        if u in visited:
            continue
        visited.add(u)

        if progress:
            progress(len(pages) + 1, max_pages, u)

        html = _fetch_html(session, u)
        if not html:
            continue

        text = _extract_markdown(traf, html)
        if text:
            base = slugify(urlparse(u).path or u) or f"page_{len(pages) + 1}"
            fname = base + ".md"
            n = 1
            while fname in used:
                fname = f"{base}_{n}.md"
                n += 1
            used.add(fname)
            title = _page_title(traf, html)
            with open(os.path.join(dest_dir, fname), "w") as fh:
                # Only add a title header if the extracted markdown lacks one.
                if title and not text.lstrip().startswith("#"):
                    fh.write(f"# {title}\n\n")
                fh.write(f"<!-- source: {u} -->\n\n")
                fh.write(text)
            pages.append({"url": u, "file": fname, "title": title})

        # Enqueue newly seen same-site links.
        for link in _extract_links(html, u, host, prefix):
            if link not in visited and link not in queued:
                queued.add(link)
                queue.append(link)

    if not pages:
        raise RuntimeError(
            f"Could not extract readable content from any page under {url}. "
            "The site may require JavaScript rendering or block automated access."
        )

    manifest = {
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(pages),
        "truncated": len(pages) >= max_pages or time.time() >= deadline,
        "pages": pages,
    }
    with open(os.path.join(dest_dir, "manifest.json"), "w") as fh:
        json.dump(manifest, fh, indent=2)
    return manifest


def load_manifest(dest_dir: str) -> dict | None:
    path = os.path.join(dest_dir, "manifest.json")
    try:
        with open(path) as fh:
            return json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError):
        return None
