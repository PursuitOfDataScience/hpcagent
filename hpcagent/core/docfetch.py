"""Mirror an online documentation site into a local markdown cache.

Given a URL to a docs/user-guide site, discover its pages (sitemap first, then a
focused crawl as fallback), extract each to markdown, and write them to a local
directory so the agent can read them instantly and offline. The source URL is
recorded in a manifest so the mirror can be re-synced later.
"""

import json
import os
import re
from datetime import datetime, timezone
from urllib.parse import urlparse

DOCS_CACHE_ROOT = os.path.expanduser("~/.cache/hpcagent/docs")


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


def discover_doc_urls(url: str, max_pages: int = 100) -> list[str]:
    """Return page URLs to mirror: sitemap first, then a focused crawl."""
    traf = _load_trafilatura()
    if traf is None:
        return []

    parsed = urlparse(url)
    host = parsed.netloc
    prefix = parsed.path
    if prefix and not prefix.endswith("/"):
        prefix = prefix.rsplit("/", 1)[0] + "/"

    def _same_host(u: str) -> bool:
        return urlparse(u).netloc == host

    def _prefer_prefix(urls: list[str]) -> list[str]:
        if prefix and len(prefix) > 1:
            under = [u for u in urls if urlparse(u).path.startswith(prefix)]
            if len(under) >= 3:
                return under
        return urls

    urls: list[str] = []

    # 1) Sitemap (most complete when present)
    try:
        from trafilatura import sitemaps
        homepage = f"{parsed.scheme}://{host}"
        sm = sitemaps.sitemap_search(homepage) or []
        urls = [u for u in sm if _same_host(u)]
        urls = _prefer_prefix(urls)
    except Exception:
        urls = []

    # 2) Fallback: focused crawl within the same domain
    if not urls:
        try:
            from trafilatura import spider
            to_visit, known = spider.focused_crawler(
                url, max_seen_urls=max_pages, max_known_urls=max_pages * 3,
            )
            cand = list(known or []) or list(to_visit or [])
            urls = [u for u in cand if _same_host(u)]
            urls = _prefer_prefix(urls)
        except Exception:
            urls = []

    def _norm(u: str) -> str:
        p = urlparse(u)
        path = p.path
        if path == "/":
            path = ""
        elif len(path) > 1 and path.endswith("/"):
            path = path.rstrip("/")
        return p._replace(path=path, fragment="", query="").geturl()

    # Always include the seed page; dedupe (normalizing trailing slashes); cap.
    if url not in urls:
        urls.insert(0, url)
    seen: set[str] = set()
    ordered: list[str] = []
    for u in urls:
        nu = _norm(u)
        if nu not in seen:
            seen.add(nu)
            ordered.append(nu)
    return ordered[:max_pages]


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


def mirror_docs(url: str, dest_dir: str | None = None, max_pages: int = 100,
                progress=None) -> dict:
    """Crawl ``url`` into a local markdown mirror. Returns a manifest dict.

    ``progress`` is an optional callable ``(index, total, url)`` for UI updates.
    Raises RuntimeError if trafilatura is missing or no pages can be fetched.
    """
    traf = _load_trafilatura()
    if traf is None:
        raise RuntimeError(
            "The 'trafilatura' package is required to mirror docs from a URL. "
            "Install with: pip install trafilatura"
        )

    dest_dir = dest_dir or mirror_dir_for(url)
    urls = discover_doc_urls(url, max_pages=max_pages)
    if not urls:
        raise RuntimeError(f"Could not discover any pages to mirror from {url}.")

    os.makedirs(dest_dir, exist_ok=True)
    # Clear any previous mirror so removed pages don't linger.
    for fn in os.listdir(dest_dir):
        if fn.endswith(".md"):
            try:
                os.remove(os.path.join(dest_dir, fn))
            except OSError:
                pass

    pages = []
    used: set[str] = set()
    total = len(urls)
    for i, u in enumerate(urls, 1):
        if progress:
            progress(i, total, u)
        try:
            html = traf.fetch_url(u)
        except Exception:
            html = None
        if not html:
            continue
        text = _extract_markdown(traf, html)
        if not text:
            continue

        title = ""
        try:
            meta = traf.extract_metadata(html)
            if meta and getattr(meta, "title", None):
                title = meta.title
        except Exception:
            pass

        base = slugify(urlparse(u).path or u) or f"page_{i}"
        fname = base + ".md"
        n = 1
        while fname in used:
            fname = f"{base}_{n}.md"
            n += 1
        used.add(fname)

        with open(os.path.join(dest_dir, fname), "w") as fh:
            if title:
                fh.write(f"# {title}\n\n")
            fh.write(f"<!-- source: {u} -->\n\n")
            fh.write(text)
        pages.append({"url": u, "file": fname, "title": title})

    if not pages:
        raise RuntimeError(
            f"Discovered pages for {url} but could not extract readable content "
            "from any of them."
        )

    manifest = {
        "source_url": url,
        "fetched_at": datetime.now(timezone.utc).isoformat(),
        "page_count": len(pages),
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
