import re
from datetime import datetime, timezone


URL_PATTERN = re.compile(r"https?://[^\s)>\]\"']+")
YEAR_PATTERN = re.compile(r"\b(20\d{2})\b")
MONTH_NAME_PATTERN = re.compile(
    r"\b(january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\b",
    re.IGNORECASE
)
MONTH_YEAR_PATTERN = re.compile(
    r"\b(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+\d{1,2},?\s+(20\d{2})\b",
    re.IGNORECASE
)
RELATIVE_MONTH_YEAR_PATTERN = re.compile(
    r"\b(?:early|mid|late)\s+(?:january|february|march|april|may|june|july|august|september|october|november|december|"
    r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec)\s+(20\d{2})\b",
    re.IGNORECASE
)

TIME_SENSITIVE_KEYWORDS = (
    "latest", "now", "current", "today", "right now", "breaking",
    "market", "stock", "stocks", "dow", "nasdaq", "s&p", "sp500",
    "weather", "news", "headlines", "price", "crypto", "bitcoin",
    "eth", "ethereum", "oil", "gold", "exchange rate", "inflation",
)

TIME_CRITICAL_HINTS = ("latest", "now", "today", "current", "right now")

STOCK_QUERY_KEYWORDS = ("stock", "stocks", "market", "dow", "nasdaq", "s&p", "sp500", "index", "indices")
WEATHER_QUERY_KEYWORDS = ("weather", "temperature", "forecast", "rain", "snow", "humidity", "wind", "storm")
NEWS_QUERY_KEYWORDS = ("news", "headlines", "breaking", "global news", "latest news")

STALE_RESPONSE_MARKERS = (
    "may be slightly dated",
    "for real-time data",
    "for realtime data",
    "for live data",
    "check yahoo finance",
    "check bloomberg",
    "check marketwatch",
    "for the latest quotes",
)

PREFERRED_FRESH_DOMAINS = (
    "reuters.com", "apnews.com", "bloomberg.com", "cnbc.com",
    "marketwatch.com", "finance.yahoo.com", "weather.com",
    "accuweather.com", "weather.gov", "wunderground.com",
    "bbc.com", "cnn.com", "nytimes.com",
)


def is_time_sensitive_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(keyword in q for keyword in TIME_SENSITIVE_KEYWORDS)


def is_stock_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(keyword in q for keyword in STOCK_QUERY_KEYWORDS)


def is_weather_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(keyword in q for keyword in WEATHER_QUERY_KEYWORDS)


def is_news_query(user_query: str) -> bool:
    q = (user_query or "").lower()
    return any(keyword in q for keyword in NEWS_QUERY_KEYWORDS)


def extract_years(text: str):
    return [int(y) for y in YEAR_PATTERN.findall(text or "")]


def count_current_year_hits(text: str) -> int:
    current_year = datetime.now(timezone.utc).year
    return sum(1 for y in extract_years(text) if y == current_year)


def has_recent_date_signals(text: str) -> bool:
    if not text:
        return False
    now_year = datetime.now(timezone.utc).year
    years = [int(y) for y in YEAR_PATTERN.findall(text)]
    has_current_or_prev_year = any(y in {now_year, now_year - 1} for y in years)
    has_month = bool(MONTH_NAME_PATTERN.search(text))
    return has_current_or_prev_year or has_month


def is_stale_for_time_sensitive_query(user_query: str, response_text: str) -> bool:
    if not is_time_sensitive_query(user_query):
        return False
    q = (user_query or "").lower()
    if not any(hint in q for hint in TIME_CRITICAL_HINTS):
        return False
    t = (response_text or "").lower()
    if any(marker in t for marker in STALE_RESPONSE_MARKERS):
        return True
    current_year = datetime.now(timezone.utc).year
    month_years = [int(y) for y in MONTH_YEAR_PATTERN.findall(response_text or "")]
    month_years.extend(int(y) for y in RELATIVE_MONTH_YEAR_PATTERN.findall(response_text or ""))
    if month_years and max(month_years) < current_year:
        return True
    years = extract_years(response_text)
    if not years:
        return False
    if "as of" in t and max(years) < current_year:
        return True
    return max(years) < current_year
