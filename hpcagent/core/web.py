from typing import Any

DDGS = None
try:
    from ddgs import DDGS as _DDGS1
    DDGS = _DDGS1  # type: ignore
except ImportError:
    try:
        from duckduckgo_search import DDGS as _DDGS2
        DDGS = _DDGS2  # type: ignore
    except ImportError:
        pass

trafilatura: Any = None
try:
    import trafilatura
except ImportError:
    pass


def web_search(query, max_results=5, timelimit=None):
    if DDGS is None:
        return "Search tool unavailable: install the 'ddgs' package."
    try:
        with DDGS() as ddgs:
            kwargs = {"max_results": max_results}
            if timelimit:
                kwargs["timelimit"] = timelimit
            results = list(ddgs.text(query, **kwargs))
        if not results:
            return "No results found."
        formatted = []
        for i, r in enumerate(results, 1):
            formatted.append(
                f"[{i}] {r.get('title', 'No title')}\n"
                f"    {r.get('href', '')}\n"
                f"    {r.get('body', '')}"
            )
        return "\n\n".join(formatted)
    except Exception as e:
        return f"Search error: {e}"


def web_fetch(url: str) -> str:
    if trafilatura is None:
        return "Fetch tool unavailable: install the 'trafilatura' package."
    try:
        downloaded = trafilatura.fetch_url(url)
        if not downloaded:
            return "Failed to download the page."
        text = trafilatura.extract(downloaded, include_tables=True)
        if not text:
            return "Could not extract readable content from the page."
        return text[:4000]
    except Exception as e:
        return f"Fetch error: {e}"
