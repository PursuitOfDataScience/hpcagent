from typing import Any
from urllib.parse import urlparse

from hpcagent.core.recency import (
    PREFERRED_FRESH_DOMAINS,
    URL_PATTERN,
    count_current_year_hits,
    extract_years,
    has_recent_date_signals,
    is_time_sensitive_query,
)
from hpcagent.core.ui import tool_status

DDGS: Any = None
try:
    from ddgs import DDGS
except ImportError:
    try:
        from duckduckgo_search import DDGS
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


def extract_candidate_urls(text: str, limit: int = 3):
    urls = []
    seen = set()
    for match in URL_PATTERN.finditer(text or ""):
        url = match.group(0).rstrip(".,;:")
        if url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def rank_url_for_freshness(url: str, user_query: str) -> int:
    score = 0
    q = (user_query or "").lower()
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    full = url.lower()

    for idx, domain in enumerate(PREFERRED_FRESH_DOMAINS):
        if domain in host:
            score += 100 - idx
            break

    if any(tok in full for tok in ("live", "latest", "today", "breaking", "markets", "weather")):
        score += 15

    if any(tok in q for tok in ("stock", "market", "dow", "nasdaq", "s&p")) and any(
        tok in host for tok in ("yahoo", "marketwatch", "cnbc", "reuters", "bloomberg")
    ):
        score += 20

    if "weather" in q and any(tok in host for tok in ("weather.com", "accuweather", "weather.gov", "wunderground")):
        score += 20

    return score


def is_fetch_failure_text(text: str) -> bool:
    if not text:
        return True
    t = text.lower().strip()
    return (
        t.startswith("failed to download the page.")
        or t.startswith("fetch error:")
        or t.startswith("could not extract readable content")
        or t.startswith("search error:")
    )


def filter_entries_for_recency(fetched_entries, user_query: str):
    if not fetched_entries:
        return fetched_entries
    if not is_time_sensitive_query(user_query):
        return fetched_entries[:3]

    now = __import__('datetime').datetime.now(__import__('datetime').timezone.utc)
    current_year = now.year
    strong_recent = []
    weak_recent = []
    stale = []

    for entry in fetched_entries:
        text = entry.get("text", "")
        years = extract_years(text)
        max_year = max(years) if years else None
        if max_year == current_year:
            strong_recent.append(entry)
        elif entry.get("has_recent_signals"):
            weak_recent.append(entry)
        else:
            stale.append(entry)

    if strong_recent:
        strong_recent.sort(key=lambda e: count_current_year_hits(e.get("text", "")), reverse=True)
        return (strong_recent + weak_recent)[:3]
    if weak_recent:
        return (weak_recent + stale)[:3]
    return stale[:3]


def build_external_web_context(
    user_query: str,
    search_query_used: str,
    search_result: str,
    fetched_entries,
    preemptive: bool = False,
) -> str:
    from datetime import datetime, timezone
    iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    natural_date = f"{datetime.now(timezone.utc).strftime('%B')} {datetime.now(timezone.utc).day}, {datetime.now(timezone.utc).year}"

    if fetched_entries:
        rendered_entries = []
        for idx, entry in enumerate(fetched_entries, 1):
            freshness_tag = "freshness-signals=yes" if entry.get("has_recent_signals") else "freshness-signals=no"
            rendered_entries.append(
                f"[SOURCE {idx}] {entry.get('url', 'N/A')} ({freshness_tag})\n{entry.get('text', '')[:2200]}"
            )
        fetched_context = "\n\n".join(rendered_entries)
    else:
        fetched_context = "No fetched content available."

    intro = (
        "Use the web context below to answer this query directly."
        if preemptive
        else "The previous answer was insufficient for this query. You must answer using the web context below."
    )

    return (
        f"{intro}\n\n"
        "Do not refuse due to scope. Provide a direct answer with concrete facts from the fetched content.\n"
        "Use the provided context directly and avoid extra tool calls unless the fetched context is clearly empty.\n"
        f"Current date reference: {iso_date} ({natural_date}).\n"
        "If the fetched sources appear stale or conflicting, explicitly say the data may be delayed and summarize "
        "what is most recent among the fetched sources.\n"
        "For time-sensitive finance/news/weather questions, prioritize the most recent timestamps/dates available.\n\n"
        "Avoid presenting old snapshots as current. If the best fetched data is from a prior year, explicitly say "
        "it is not current and provide the most recent available context.\n\n"
        f"User query: {user_query}\n\n"
        f"Search query used: {search_query_used}\n\n"
        f"Search results:\n{search_result}\n\n"
        f"Fetched page content:\n{fetched_context}"
    )


def build_freshness_search_query(user_query: str) -> str:
    from datetime import datetime, timezone
    query = (user_query or "").strip()
    if not query:
        return query
    if not is_time_sensitive_query(query):
        return query

    iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    natural_date = f"{datetime.now(timezone.utc).strftime('%B')} {datetime.now(timezone.utc).day}, {datetime.now(timezone.utc).year}"
    year = datetime.now(timezone.utc).strftime("%Y")
    base_query = f"{query} latest {iso_date} {natural_date} {year}"

    from hpcagent.core.recency import is_news_query, is_stock_query, is_weather_query
    if is_stock_query(query):
        return f"{base_query} dow jones nasdaq s&p 500 live reuters cnbc marketwatch yahoo finance"
    if is_weather_query(query):
        return f"{base_query} live conditions weather.com accuweather weather.gov"
    if is_news_query(query):
        return f"{base_query} reuters apnews bbc cnn latest headlines"
    return base_query


def gather_external_web_context(user_query: str):
    from datetime import datetime, timezone

    from hpcagent.core.recency import is_time_sensitive_query

    tool_status("web_search", status="running")
    search_query = build_freshness_search_query(user_query)
    is_time_sensitive = is_time_sensitive_query(user_query)
    search_result = web_search(
        search_query,
        max_results=8 if is_time_sensitive else 5,
        timelimit="d" if is_time_sensitive else None,
    )
    tool_status("web_search", status="success")

    fetched_entries = []
    urls = extract_candidate_urls(search_result, limit=8 if is_time_sensitive else 3)
    urls = sorted(urls, key=lambda u: rank_url_for_freshness(u, user_query), reverse=True)

    max_fetch = 4 if is_time_sensitive else 3
    failed_entries = []
    fetch_status_started = False
    fetch_attempted = 0
    fetch_success = 0

    from hpcagent.core.ui import c

    def fetch_entry_from_url(fetched_url: str):
        nonlocal fetch_status_started, fetch_attempted, fetch_success
        if not fetch_status_started:
            print(f"\r{c.GRAY}... Calling web_fetch...\033[K{c.RESET}", end="", flush=True)
            fetch_status_started = True
        fetch_attempted += 1
        fetched_text = web_fetch(fetched_url)
        entry = {
            "url": fetched_url,
            "text": fetched_text,
            "has_recent_signals": has_recent_date_signals(fetched_text),
        }
        if not is_fetch_failure_text(fetched_text):
            fetch_success += 1
        return entry

    for fetched_url in urls[:max_fetch + 2]:
        entry = fetch_entry_from_url(fetched_url)
        if is_fetch_failure_text(entry.get("text", "")):
            failed_entries.append(entry)
            continue
        fetched_entries.append(entry)
        if len(fetched_entries) >= max_fetch:
            break

    if not fetched_entries and failed_entries:
        fetched_entries.extend(failed_entries[:2])

    if is_time_sensitive and not any(entry.get("has_recent_signals") for entry in fetched_entries):
        iso_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        natural_date = f"{datetime.now(timezone.utc).strftime('%B')} {datetime.now(timezone.utc).day}, {datetime.now(timezone.utc).year}"
        alternate_query = f"{user_query} live updates {iso_date} {natural_date}"
        tool_status("web_search", status="running")
        alt_result = web_search(alternate_query, max_results=8, timelimit="d")
        tool_status("web_search", status="success")
        search_result = f"{search_result}\n\n--- ALTERNATE SEARCH ---\n{alt_result}"
        existing_urls = {entry.get("url") for entry in fetched_entries}
        alt_urls = extract_candidate_urls(alt_result, limit=8)
        alt_urls = sorted(alt_urls, key=lambda u: rank_url_for_freshness(u, user_query), reverse=True)
        for fetched_url in alt_urls:
            if fetched_url in existing_urls:
                continue
            entry = fetch_entry_from_url(fetched_url)
            if is_fetch_failure_text(entry.get("text", "")):
                continue
            fetched_entries.append(entry)
            existing_urls.add(fetched_url)
            if len(fetched_entries) >= max_fetch:
                break

    if fetch_status_started:
        print(
            f"\r{c.MINT}\u2713{c.RESET} {c.GRAY}"
            f"Calling web_fetch ({fetch_success}/{fetch_attempted} sources)\033[K{c.RESET}",
            end="", flush=True,
        )

    fetched_entries = filter_entries_for_recency(fetched_entries, user_query)
    return search_query, search_result, fetched_entries
