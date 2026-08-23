"""news - fetch real headlines from RSS/Atom feeds. Stdlib only.

Grounds a product brief in actual headlines instead of model-invented
stories. The default bundle intentionally spans AI, tech, business, markets,
security, research, broader news, and audio/video-friendly feeds. Override with
NEWS_FEEDS as a comma-separated URL list for a tenant or environment.
"""

from __future__ import annotations

import email.utils
import os
import re
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from xml.etree import ElementTree as ET

DEFAULT_FEED_REGISTRY = [
    {
        "url": "https://hnrss.org/frontpage",
        "source": "Hacker News",
        "category": "Developer + Startup Signals",
        "kind": "community",
    },
    {
        "url": "https://feeds.arstechnica.com/arstechnica/index",
        "source": "Ars Technica",
        "category": "Technology",
        "kind": "article",
    },
    {
        "url": "https://www.theverge.com/rss/index.xml",
        "source": "The Verge",
        "category": "Technology",
        "kind": "article",
    },
    {
        "url": "https://techcrunch.com/feed/",
        "source": "TechCrunch",
        "category": "Startups + Venture",
        "kind": "article",
    },
    {
        "url": "https://www.wired.com/feed/rss",
        "source": "WIRED",
        "category": "Technology + Culture",
        "kind": "article",
    },
    {
        "url": "https://www.technologyreview.com/feed/",
        "source": "MIT Technology Review",
        "category": "AI + Research",
        "kind": "article",
    },
    {
        "url": "https://venturebeat.com/category/ai/feed/",
        "source": "VentureBeat AI",
        "category": "AI + Applied Automation",
        "kind": "article",
    },
    {
        "url": "https://openai.com/news/rss.xml",
        "source": "OpenAI News",
        "category": "AI Labs",
        "kind": "article",
    },
    {
        "url": "https://deepmind.google/blog/rss.xml",
        "source": "Google DeepMind Blog",
        "category": "AI Labs",
        "kind": "article",
    },
    {
        "url": "https://deepmind.google/blog/rss.xml",
        "source": "Google DeepMind Blog",
        "category": "AI Labs",
        "kind": "article",
    },
    {
        "url": "https://github.blog/feed/",
        "source": "GitHub Blog",
        "category": "Developer Tools",
        "kind": "article",
    },
    {
        "url": "https://www.theregister.com/software/headlines.atom",
        "source": "The Register Software",
        "category": "Developer Tools",
        "kind": "article",
    },
    {
        "url": "https://krebsonsecurity.com/feed/",
        "source": "Krebs on Security",
        "category": "Security",
        "kind": "article",
    },
    {
        "url": "https://feeds.feedburner.com/TheHackersNews",
        "source": "The Hacker News",
        "category": "Security",
        "kind": "article",
    },
    {
        "url": "https://feeds.npr.org/1001/rss.xml",
        "source": "NPR News",
        "category": "Current Events",
        "kind": "article",
    },
    {
        "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html",
        "source": "CNBC Top News",
        "category": "Business + Markets",
        "kind": "article",
    },
    {
        "url": "https://feeds.content.dowjones.io/public/rss/mw_topstories",
        "source": "MarketWatch Top Stories",
        "category": "Markets",
        "kind": "article",
    },
    {
        "url": "https://rss.arxiv.org/rss/cs.AI",
        "source": "arXiv cs.AI",
        "category": "Research Papers",
        "kind": "paper",
    },
    {
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCsBjURrPoezykLs9EqgamOA",
        "source": "Fireship",
        "category": "Developer Video",
        "kind": "video",
    },
    {
        "url": "https://www.youtube.com/feeds/videos.xml?channel_id=UCXZCJLdBC09xxGZ6gcdrc6A",
        "source": "OpenAI",
        "category": "Longform AI + Science",
        "kind": "video",
    },
]


def _host(url: str) -> str:
    try:
        return urllib.parse.urlparse(url).netloc.replace("www.", "")
    except Exception:
        return url


def feed_registry() -> list[dict]:
    """Return configured feed metadata.

    NEWS_FEEDS remains intentionally simple: comma-separated URLs. Custom feeds
    get inferred source names and land in the Custom News category.
    """
    raw = os.environ.get("NEWS_FEEDS", "").strip()
    if not raw:
        return [dict(item) for item in DEFAULT_FEED_REGISTRY]
    return [
        {
            "url": url.strip(),
            "source": _host(url.strip()),
            "category": "Custom News",
            "kind": "article",
        }
        for url in raw.split(",")
        if url.strip()
    ]


def source_catalog() -> list[dict]:
    """A UI/API-safe summary of the default source lanes, without secrets."""
    counts: dict[tuple[str, str], int] = defaultdict(int)
    examples: dict[tuple[str, str], list[str]] = defaultdict(list)
    for feed in feed_registry():
        key = (feed["category"], feed["kind"])
        counts[key] += 1
        if len(examples[key]) < 3:
            examples[key].append(feed["source"])
    return [
        {"category": category, "kind": kind, "feeds": count, "examples": examples[(category, kind)]}
        for (category, kind), count in sorted(counts.items())
    ]


def feeds() -> list[str]:
    return [item["url"] for item in feed_registry()]


def _parse_date(text: str | None) -> datetime | None:
    """Parse an RSS pubDate (RFC 822) or Atom timestamp (ISO 8601). None if absent."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        dt = email.utils.parsedate_to_datetime(text)
        if dt is not None:
            return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError, IndexError):
        pass
    try:
        dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _iso(dt: datetime | None) -> str:
    return dt.isoformat() if dt else ""


# Query keys that carry tracking, not identity — stripped so the same story from two
# links dedupes.
_TRACKING = ("utm_", "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "cmpid", "spm", "igshid")


def _canonical_url(url: str) -> str:
    url = (url or "").strip()
    if not url:
        return ""
    try:
        parts = urllib.parse.urlsplit(url)
    except ValueError:
        return url
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query)
             if not any(k.lower().startswith(p) for p in _TRACKING)]
    path = parts.path.rstrip("/") or "/"
    return urllib.parse.urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), path, urllib.parse.urlencode(query), ""))


_STOPWORDS = frozenset(
    "the a an and or of to in on for with is are at by from as how why what this that "
    "it its new now over amid into after says will can".split())


def _title_tokens(title: str) -> frozenset:
    """Significant words in a headline, for near-duplicate story clustering."""
    words = re.findall(r"[a-z0-9]+", (title or "").lower())
    return frozenset(w for w in words if len(w) > 2 and w not in _STOPWORDS)


def parse_rss(xml_text: str, source: str = "", category: str = "News", kind: str = "article") -> list[dict]:
    """Parse RSS 2.0 or Atom into headline dicts. Tolerant of both.

    Each item carries a `published` ISO-8601 string when the feed provides one
    (RSS <pubDate> / Atom <published>|<updated>), so callers can rank by freshness.
    """
    items: list[dict] = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return items

    for it in root.iter("item"):
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if title:
            published = it.findtext("pubDate") or it.findtext("{http://purl.org/dc/elements/1.1/}date")
            items.append({"title": title, "url": link, "source": source, "category": category,
                          "kind": kind, "published": _iso(_parse_date(published))})
    if items:
        return items

    ns = "{http://www.w3.org/2005/Atom}"
    for entry in root.iter(f"{ns}entry"):
        title_el = entry.find(f"{ns}title")
        title = (title_el.text or "").strip() if title_el is not None else ""
        link = ""
        for le in entry.findall(f"{ns}link"):
            if le.get("rel", "alternate") == "alternate" or not link:
                link = le.get("href", "") or link
        if title:
            published = entry.findtext(f"{ns}published") or entry.findtext(f"{ns}updated")
            items.append({"title": title, "url": link, "source": source, "category": category,
                          "kind": kind, "published": _iso(_parse_date(published))})
    return items


def _dedupe(items: list[dict], similarity: float = 0.6) -> list[dict]:
    """Collapse exact and near-duplicate stories.

    Exact: same canonical URL (tracking params stripped). Near-dup: headlines whose
    significant-word sets overlap >= `similarity` (Jaccard) are the same story across
    outlets. Survivors accumulate a `sources` list and `source_count` so the brief can
    surface cross-source agreement ("covered by N sources") — a real trust signal.
    """
    out: list[dict] = []
    seen_urls: set[str] = set()
    token_sets: list[frozenset] = []
    for item in items:
        curl = _canonical_url(item.get("url", ""))
        if curl and curl in seen_urls:
            continue
        toks = _title_tokens(item.get("title", ""))
        merged_into = -1
        for i, prev in enumerate(token_sets):
            if toks and prev:
                union = len(toks | prev)
                if union and len(toks & prev) / union >= similarity:
                    merged_into = i
                    break
        if merged_into >= 0:
            src = item.get("source", "")
            srcs = out[merged_into]["sources"]
            if src and src not in srcs:
                srcs.append(src)
                out[merged_into]["source_count"] = len(srcs)
            continue
        if curl:
            seen_urls.add(curl)
        row = dict(item)
        row["sources"] = [item["source"]] if item.get("source") else []
        row["source_count"] = len(row["sources"])
        out.append(row)
        token_sets.append(toks)
    return out


def _round_robin_by_category(items: list[dict], limit: int) -> list[dict]:
    buckets: dict[str, list[dict]] = defaultdict(list)
    for item in items:
        buckets[item.get("category") or "News"].append(item)
    for bucket in buckets.values():
        # Freshest first; undated items keep feed order and sort last (stable sort).
        bucket.sort(key=lambda it: it.get("published") or "", reverse=True)
    out: list[dict] = []
    while len(out) < limit and any(buckets.values()):
        for category in sorted(list(buckets.keys())):
            if buckets[category]:
                out.append(buckets[category].pop(0))
                if len(out) >= limit:
                    break
    return out


def _within_age(item: dict, cutoff_ts: float) -> bool:
    published = item.get("published")
    if not published:
        return True  # keep undated items — never silently drop a real headline
    dt = _parse_date(published)
    return dt is None or dt.timestamp() >= cutoff_ts


def fetch_headlines(limit: int = 12, timeout: int = 10, max_age_hours: int | None = None) -> list[dict]:
    """Fetch + merge headlines from configured feeds.

    Best-effort: failed feeds are skipped, never raised. Results are deduped (incl.
    near-duplicate stories across outlets), optionally filtered to the last
    `max_age_hours`, and mixed by category — freshest first — so the first page is
    not monopolized by one publisher or by stale items.
    """
    items: list[dict] = []
    per_feed = max(2, min(4, limit // 3 or 2))
    for feed in feed_registry():
        try:
            req = urllib.request.Request(feed["url"], headers={"User-Agent": "Prepende/1.0"})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                xml = r.read().decode("utf-8", "replace")
            items.extend(parse_rss(
                xml,
                source=feed.get("source") or _host(feed["url"]),
                category=feed.get("category") or "News",
                kind=feed.get("kind") or "article",
            )[:per_feed])
        except Exception:
            continue
    deduped = _dedupe(items)
    if max_age_hours is not None:
        cutoff = datetime.now(timezone.utc).timestamp() - max_age_hours * 3600
        deduped = [it for it in deduped if _within_age(it, cutoff)]
    return _round_robin_by_category(deduped, limit)
