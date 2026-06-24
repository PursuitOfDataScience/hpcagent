"""Mirror an online documentation site into a local markdown cache.

Given a URL to a docs/user-guide site, discover its pages (sitemap first, then a
breadth-first crawl of same-site links), extract each to markdown, and write them
to a local directory so the agent can read them instantly and offline. The source
URL is recorded in a manifest so the mirror can be re-synced later.

Pages are fetched concurrently and the crawl is bounded by both a page cap and a
wall-clock budget, so it stays fast and never hangs on large or slow sites.
"""

import gzip
import json
import os
import re
import time
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

DOCS_CACHE_ROOT = os.path.expanduser("~/.cache/hpcpilot/docs")

DEFAULT_MAX_PAGES = 400
DEFAULT_TIME_BUDGET = 300
# I/O-bound crawl: scale past core count, but stay polite to the server.
DEFAULT_WORKERS = min(32, (os.cpu_count() or 8) * 2)

# Non-document assets we never want to fetch/extract.
_SKIP_EXT = (
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".ico", ".bmp",
    ".css", ".js", ".map", ".pdf", ".zip", ".gz", ".tar", ".tgz",
    ".woff", ".woff2", ".ttf", ".eot", ".otf", ".mp4", ".mov", ".webm",
    ".mp3", ".wav", ".json", ".xml", ".csv", ".txt", ".rss", ".atom",
)

_UA = "hpcpilot-docs/0.1 (+local docs mirror)"


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


def _fetch_html(url: str, timeout: int = 12) -> str | None:
    """Fetch a URL and return HTML text, or None for non-HTML/errors."""
    import requests
    try:
        r = requests.get(url, timeout=timeout, headers={"User-Agent": _UA})
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


def _sitemap_urls(start: str, host: str, timeout: int = 10) -> list[str]:
    """Best-effort, fast sitemap read (handles .gz and sitemap-index)."""
    import requests
    urls: list[str] = []
    session = requests.Session()
    session.headers.update({"User-Agent": _UA})
    try:
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
                loc = loc.strip()
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
    return [_norm_url(u.strip()) for u in urls if urlparse(u.strip()).netloc == host]


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


def _crawl_one(args):
    """Fetch + extract a single page. Top-level so it is picklable for processes.

    Returns ``(url, markdown_or_None, title_or_None, links)``.
    """
    url, host, prefix = args
    traf = _load_trafilatura()
    if traf is None:
        return (url, None, None, [])
    html = _fetch_html(url)
    if not html:
        return (url, None, None, [])
    return (url, _extract_markdown(traf, html), _page_title(traf, html),
            _extract_links(html, url, host, prefix))


def _probe_ok():
    return True


def _make_pool(workers: int):
    """Prefer a process pool (real multi-core extraction); fall back to threads.

    Probes the process pool so we degrade gracefully in sandboxes that forbid
    forking/spawning subprocesses, rather than failing the whole crawl.
    """
    try:
        pool = ProcessPoolExecutor(max_workers=max(1, workers))
        pool.submit(_probe_ok).result(timeout=30)
        return pool, "process"
    except Exception:
        try:
            pool.shutdown(cancel_futures=True)  # type: ignore[possibly-undefined]
        except Exception:
            pass
        return ThreadPoolExecutor(max_workers=max(1, workers)), "thread"


def mirror_docs(url: str, dest_dir: str | None = None, max_pages: int = DEFAULT_MAX_PAGES,
                progress=None, time_budget: int = DEFAULT_TIME_BUDGET,
                workers: int = DEFAULT_WORKERS) -> dict:
    """Crawl ``url`` into a local markdown mirror. Returns a manifest dict.

    Pages are processed in concurrent breadth-first waves: each page is fetched
    once, extracted to markdown, and its same-site links (under the URL's path)
    feed the next wave. Seeded by the sitemap when present so whole guides are
    captured even if their in-page nav is incomplete. Bounded by ``max_pages``
    and ``time_budget`` (seconds).

    ``progress`` is an optional callable ``(saved, cap, url)`` for UI updates.
    Raises RuntimeError if trafilatura is missing or no pages can be extracted.
    """
    traf = _load_trafilatura()
    if traf is None:
        raise RuntimeError(
            "The 'trafilatura' package is required to mirror docs from a URL. "
            "Install with: pip install trafilatura  (or: pip install 'hpcpilot[full]')"
        )

    dest_dir = dest_dir or mirror_dir_for(url)
    start = _norm_url(url)
    host = urlparse(start).netloc
    # Scope from the *original* URL: normalizing strips a trailing slash, which
    # would otherwise turn a section root like /polaris/ into the whole site.
    prefix = _path_prefix(urlparse(url))

    # Seed frontier: start page + sitemap entries (filtered to the path prefix).
    seeds = _sitemap_urls(start, host)
    if prefix and len(prefix) > 1:
        under = [u for u in seeds if urlparse(u).path.startswith(prefix)]
        if len(under) >= 3:
            seeds = under
    frontier = [start] + [u for u in seeds if u != start]

    os.makedirs(dest_dir, exist_ok=True)
    for fn in os.listdir(dest_dir):
        if fn.endswith(".md"):
            try:
                os.remove(os.path.join(dest_dir, fn))
            except OSError:
                pass

    visited: set[str] = set()
    queued: set[str] = set(frontier)
    pages: list[dict[str, str]] = []
    used: set[str] = set()
    deadline = time.time() + time_budget

    pool, _kind = _make_pool(workers)
    with pool:
        while frontier and len(pages) < max_pages and time.time() < deadline:
            batch = [u for u in frontier if u not in visited][: max_pages * 2]
            visited.update(batch)
            frontier = []
            if not batch:
                break

            futures = {pool.submit(_crawl_one, (u, host, prefix)): u for u in batch}
            for fut in as_completed(futures):
                if len(pages) >= max_pages or time.time() >= deadline:
                    break
                try:
                    u, text, title, links = fut.result()
                except Exception:
                    continue
                if progress:
                    progress(len(pages) + 1, max_pages, u)
                if text:
                    base = slugify(urlparse(u).path or u) or f"page_{len(pages) + 1}"
                    fname = base + ".md"
                    n = 1
                    while fname in used:
                        fname = f"{base}_{n}.md"
                        n += 1
                    used.add(fname)
                    with open(os.path.join(dest_dir, fname), "w") as fh:
                        if title and not text.lstrip().startswith("#"):
                            fh.write(f"# {title}\n\n")
                        fh.write(f"<!-- source: {u} -->\n\n")
                        fh.write(text)
                    pages.append({"url": u, "file": fname, "title": title})
                for link in links:
                    if link not in visited and link not in queued:
                        queued.add(link)
                        frontier.append(link)

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
        "pages": sorted(pages, key=lambda p: p["url"]),
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
