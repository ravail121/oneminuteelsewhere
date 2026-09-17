"""Free, explicit trend lookups. Never use the channel OAuth token for research."""
import hashlib
import json
import os
import re
import secrets
import threading
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime

from .languages import COUNTRIES, suggest_language
from .openai_service import save_json

# Fixed, bounded discovery coverage across inhabited continents; not every country.
WORLD_REGIONS = ("US", "CA", "MX", "BR", "AR", "CL", "CO", "PE", "VE", "GB", "IE", "FR", "DE",
                 "ES", "PT", "IT", "NL", "BE", "CH", "AT", "SE", "NO", "DK", "FI", "PL", "CZ",
                 "HU", "RO", "GR", "UA", "TR", "RU", "ZA", "NG", "KE", "EG", "MA", "DZ", "SA",
                 "AE", "IL", "IN", "PK", "BD", "LK", "NP", "JP", "KR", "TW", "HK", "SG", "MY",
                 "ID", "PH", "TH", "VN", "AU", "NZ")
RANKING_METHOD = "Estimated momentum: sum of available regional search-volume lower bounds divided by hours since each trend began (minimum 1 hour). Not measured growth or a verified global/YouTube #1."


def traffic_lower_bound(value):
    match = re.fullmatch(r"\s*([\d,.]+)\s*([KMB]?)\+?\s*", value or "", re.IGNORECASE)
    if not match:
        return None
    try:
        return int(float(match[1].replace(",", "")) * {"": 1, "K": 1000, "M": 1_000_000, "B": 1_000_000_000}[match[2].upper()])
    except (ValueError, OverflowError):
        return None


def rank_topics(feeds, now):
    combined = {}
    for feed in feeds:
        for record in feed:
            key = " ".join(record["title"].casefold().split())
            topic = combined.setdefault(key, {**record, "regional_evidence": []})
            topic["regional_evidence"].append({k: record.get(k) for k in
                ("country", "source_url", "published_at", "search_volume_lower_bound")})
    for topic in combined.values():
        evidence = topic["regional_evidence"]
        known = [item for item in evidence if item["search_volume_lower_bound"] is not None]
        topic["momentum_estimate"] = (round(sum(item["search_volume_lower_bound"] /
            max(1, (now - datetime.fromisoformat(item["published_at"])).total_seconds()/3600)
            for item in known), 2) if known else None)
        topic["regions_seen"] = len(evidence)
        topic["missing_volume_regions"] = len(evidence) - len(known)
        topic["ranking_reason"] = (RANKING_METHOD if known else
            "Search volume unavailable: recent-topic fallback only; cannot establish the hottest topic.")
    return sorted(combined.values(), key=lambda t: (t["momentum_estimate"] is not None,
        t["momentum_estimate"] or 0, t["regions_seen"], t["published_at"], t["title"]), reverse=True)[:10]


class TrendError(ValueError):
    pass


def fetch_public(url, api_key=None):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname not in {"trends.google.com", "www.googleapis.com"}:
        raise TrendError("Unsupported trend source")
    headers = {"User-Agent": "OneMinuteElsewhereLocal/1.0"}
    if api_key:
        headers["X-Goog-Api-Key"] = api_key
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            raise TrendError("Trend source redirected; no credentials were forwarded")
    try:
        opener = urllib.request.build_opener(NoRedirect)
        with opener.open(urllib.request.Request(url, headers=headers), timeout=5) as response:
            data = response.read(2_000_001)
            if len(data) > 2_000_000:
                raise TrendError("Trend source response is too large")
            return data
    except Exception:  # noqa: BLE001 - never expose URLs/headers/keys from provider errors
        raise TrendError("Trend source unavailable or quota exhausted. No paid request was made; try later.") from None


def parse_feed(data, country, now):
    if b"\x00" in data or b"<!DOCTYPE" in data.upper() or b"<!ENTITY" in data.upper():
        raise TrendError("Unsupported feed format")
    try:
        root = ET.fromstring(data)
    except ET.ParseError:
        raise TrendError("Trend source returned an invalid feed") from None
    result = []
    for item in root.findall("./channel/item")[:100]:
        title = " ".join((item.findtext("title") or "").split())[:200]
        try:
            published = parsedate_to_datetime(item.findtext("pubDate") or "").astimezone(UTC)
        except (ValueError, TypeError, AttributeError):
            continue
        if not title or not now - timedelta(hours=24) <= published <= now + timedelta(minutes=5):
            continue
        # Conservative topical screening, not a claim of semantic safety.
        if re.search(r"\b(killed|murder|rape|suicide|war|attack|porn|election|shooting|death)\b", title, re.IGNORECASE):
            continue
        code, reason = suggest_language(title)
        volume = item.findtext("{*}approx_traffic")
        result.append({"id": hashlib.sha256((country + title + published.isoformat()).encode()).hexdigest()[:32],
                       "search_volume_lower_bound": traffic_lower_bound(volume),
                       "title": title, "country": country, "published_at": published.isoformat(),
                       "suggested_language": code, "language_reason": reason,
                       "source": "Google Trends — Google Search interest, not YouTube search rankings",
                       "source_url": "https://trends.google.com/trending?geo=" + country,
                       "youtube": [], "youtube_status": "Not checked — backend YouTube Data API key not configured"})
    return result  # Rank all bounded feed entries; do not discard a later high-volume topic.


class TrendStore:
    def __init__(self, root, fetcher=fetch_public):
        self.directory = root / "data/trends"
        if self.directory.is_symlink():
            raise TrendError("Unsafe trend state directory")
        self.directory.mkdir(parents=True, exist_ok=True)
        self.fetcher = fetcher
        self.lock = threading.Lock()

    def lookup(self, country="AUTO"):
        if country not in {*COUNTRIES, *WORLD_REGIONS, "AUTO"}:
            raise TrendError("Choose a supported country")
        with self.lock:
            now = datetime.now(UTC)
            path = self.directory / (country + "-world-momentum-v3.json")
            if path.is_symlink():
                raise TrendError("Unsafe trend cache path")
            if path.exists():
                cached = json.loads(path.read_text())
                if now - datetime.fromisoformat(cached["fetched_at"]) < timedelta(minutes=30):
                    cached["records"] = [r for r in cached["records"] if now - datetime.fromisoformat(r["published_at"]) <= timedelta(hours=24)]
                    return cached
            sources = list(WORLD_REGIONS) if country == "AUTO" else [country]
            feeds, unavailable = [], []
            def get_feed(source):
                return parse_feed(self.fetcher("https://trends.google.com/trending/rss?geo=" + source), source, now)
            # Request-scoped workers only. No scheduler, permanent worker or paid calls.
            with ThreadPoolExecutor(max_workers=8) as pool:
                futures = {pool.submit(get_feed, source): source for source in sources}
                try:
                    for future in as_completed(futures, timeout=30):
                        source = futures[future]
                        try:
                            feeds.append((source, future.result()))
                        except (TrendError, ValueError, TypeError):
                            unavailable.append(source)
                except TimeoutError:
                    for future in futures:
                        future.cancel()
                    completed = {source for source, _ in feeds} | set(unavailable)
                    unavailable.extend(s for s in sources if s not in completed)
            if len(unavailable) == len(sources):
                raise TrendError("Trend sources are unavailable. No paid request was made; try later.")
            records = rank_topics([items for _, items in sorted(feeds)], now)
            key = os.environ.get("YOUTUBE_DATA_API_KEY", "")
            # At most three searches per refresh; no model calls or OAuth scopes.
            if key:
                for record in records[:3]:
                    params = urllib.parse.urlencode({"part": "snippet", "type": "video", "q": record["title"],
                        "publishedAfter": (now - timedelta(hours=24)).isoformat(),
                        "order": "relevance", "maxResults": 3, "safeSearch": "strict"})
                    try:
                        response = json.loads(self.fetcher("https://www.googleapis.com/youtube/v3/search?" + params, api_key=key))
                        record["youtube"] = [{"title": str(i.get("snippet", {}).get("title", ""))[:200],
                            "url": "https://www.youtube.com/watch?v=" + i["id"]["videoId"]}
                            for i in response.get("items", [])[:3]
                            if re.fullmatch(r"[A-Za-z0-9_-]{11}", i.get("id", {}).get("videoId", ""))]
                        record["youtube_status"] = "Recent YouTube matches found; not proof of virality" if record["youtube"] else "No recent YouTube matches found"
                    except (TrendError, ValueError, TypeError, KeyError):
                        record["youtube_status"] = "YouTube check unavailable; no ranking claimed"
                for record in records[3:]:
                    record["youtube_status"] = "Not checked — three-search refresh limit"
            snapshot = {"id": secrets.token_hex(16), "country": country, "window_hours": 24,
                        "discovery_sources": sources, "unavailable_sources": sorted(unavailable),
                        "sources_responded": len(feeds), "ranking_method": RANKING_METHOD,
                        "language_selection": "topic_context_v2",
                        "fetched_at": now.isoformat(), "records": records, "calculated_cost_usd": 0.0,
                        "cost_basis": "Public RSS and optional quota-based YouTube lookup; no paid AI research",
                        "pricing_sources": ["https://support.google.com/trends/answer/3076011",
                                            "https://developers.google.com/youtube/v3/getting-started"]}
            save_json(self.directory / (snapshot["id"] + ".json"), snapshot)
            save_json(path, snapshot)
            return snapshot

    def selection(self, snapshot_id, topic_id):
        if not all(re.fullmatch(r"[a-f0-9]{32}", value or "") for value in (snapshot_id, topic_id)):
            raise TrendError("Choose a topic from the saved trend results")
        path = self.directory / (snapshot_id + ".json")
        if not path.is_file() or path.is_symlink():
            raise TrendError("Trend results unavailable; fetch topics again")
        snapshot = json.loads(path.read_text())
        if datetime.now(UTC) - datetime.fromisoformat(snapshot["fetched_at"]) > timedelta(hours=24):
            raise TrendError("These results are stale. Fetch current trends before starting a new project.")
        for record in snapshot["records"]:
            if record["id"] == topic_id:
                if datetime.now(UTC) - datetime.fromisoformat(record["published_at"]) > timedelta(hours=24):
                    raise TrendError("This topic is older than 24 hours. Fetch current topics again.")
                code, reason = suggest_language(record["title"])
                return {**record, "suggested_language": code, "language_reason": reason,
                        "discovery_sources": snapshot.get("discovery_sources", []),
                        "unavailable_sources": snapshot.get("unavailable_sources", []),
                        "ranking_method": snapshot.get("ranking_method"),
                        "language_selection": "topic_context_v2", "fetched_at": snapshot["fetched_at"], "window_hours": 24,
                        "research_cost_usd": 0.0, "cost_basis": snapshot["cost_basis"]}
        raise TrendError("Unknown trend topic")
