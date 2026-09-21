"""Fetch and parse front-end job listings from public sources."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup
from dateutil import parser as date_parser
from dateutil.relativedelta import relativedelta

USER_AGENT = (
    "FrontlineJobScraper/1.0 (+https://github.com/; portfolio demo; polite scrape)"
)
REQUEST_TIMEOUT = 30
MAX_RESPONSE_BYTES = 2_000_000

DAY_OPTIONS = (1, 3, 7, 14, 30)

FRONTEND_TAG_HINTS = {
    "frontend",
    "front-end",
    "front end",
    "react",
    "vue",
    "angular",
    "svelte",
    "nextjs",
    "next.js",
}

FRONTEND_TITLE_HINTS = (
    "front-end",
    "frontend",
    "front end",
    "full stack",
    "full-stack",
    "fullstack",
    "react",
    "vue",
    "angular",
    "svelte",
    "next.js",
    "nextjs",
    "ui engineer",
    "ui developer",
    "web developer",
    "javascript developer",
    "typescript developer",
    "css engineer",
)

PRESETS = {
    "remoteok": {
        "label": "RemoteOK — front-end",
        "url": "https://remoteok.com/remote-frontend-jobs",
        "feed": "https://remoteok.com/api",
        "kind": "remoteok_api",
    },
    "remotive": {
        "label": "Remotive — front-end titles",
        "url": "https://remotive.com/remote-jobs/categories/software-dev",
        "feed": "https://remotive.com/api/remote-jobs?category=software-dev",
        "kind": "remotive_api",
    },
    "wwr": {
        "label": "We Work Remotely — programming RSS",
        "url": "https://weworkremotely.com/categories/remote-programming-jobs",
        "feed": "https://weworkremotely.com/categories/remote-programming-jobs.rss",
        "kind": "rss",
    },
}


@dataclass
class Job:
    title: str
    company: str
    location: str
    url: str
    posted_at: Optional[datetime] = None
    posted_label: str = ""
    tags: list[str] = field(default_factory=list)


@dataclass
class ScrapeResult:
    jobs: list[Job]
    source_url: str
    error: Optional[str] = None
    filtered_out: int = 0


def scrape_jobs(
    *,
    preset: Optional[str] = None,
    custom_url: Optional[str] = None,
    days: int = 7,
) -> ScrapeResult:
    """Scrape a preset or custom URL and filter by days posted."""
    days = _normalize_days(days)
    custom = (custom_url or "").strip()

    if custom:
        ok, message = _validate_url(custom)
        if not ok:
            return ScrapeResult(jobs=[], source_url=custom, error=message)
        try:
            jobs = _scrape_custom(custom)
        except requests.Timeout:
            return ScrapeResult(
                jobs=[],
                source_url=custom,
                error="The request timed out. Try again in a moment.",
            )
        except requests.RequestException as exc:
            return ScrapeResult(
                jobs=[],
                source_url=custom,
                error=f"Could not reach that page ({exc.__class__.__name__}).",
            )
        except (ValueError, json.JSONDecodeError):
            return ScrapeResult(
                jobs=[],
                source_url=custom,
                error="Could not parse listings from that URL.",
            )
        return _finalize(jobs, custom, days)

    key = (preset or "").strip()
    meta = PRESETS.get(key)
    if not meta:
        if not key:
            return ScrapeResult(
                jobs=[], source_url="", error="Choose a preset or enter a listing URL."
            )
        return ScrapeResult(jobs=[], source_url="", error="Unknown preset. Pick one from the list.")

    source_url = meta["url"]
    try:
        jobs = _scrape_preset(meta)
    except requests.Timeout:
        return ScrapeResult(
            jobs=[],
            source_url=source_url,
            error="The request timed out. Try again in a moment.",
        )
    except requests.RequestException as exc:
        return ScrapeResult(
            jobs=[],
            source_url=source_url,
            error=f"Could not reach that board ({exc.__class__.__name__}).",
        )
    except (ValueError, json.JSONDecodeError, KeyError, TypeError):
        return ScrapeResult(
            jobs=[],
            source_url=source_url,
            error="Could not parse listings from that board.",
        )

    return _finalize(jobs, source_url, days)


def _finalize(jobs: list[Job], source_url: str, days: int) -> ScrapeResult:
    if not jobs:
        return ScrapeResult(
            jobs=[],
            source_url=source_url,
            error=(
                "No job listings found. Presets work best; custom pages need "
                "recognizable listing markup."
            ),
        )
    kept, dropped = _filter_by_days(jobs, days)
    return ScrapeResult(jobs=kept, source_url=source_url, filtered_out=dropped)


def _scrape_preset(meta: dict) -> list[Job]:
    kind = meta["kind"]
    if kind == "remoteok_api":
        return _parse_remoteok_api(_fetch_bytes(meta["feed"]))
    if kind == "remotive_api":
        return _parse_remotive_api(_fetch_bytes(meta["feed"]))
    if kind == "rss":
        return _parse_rss(_fetch_bytes(meta["feed"]), meta["url"])
    raise ValueError(f"Unknown preset kind: {kind}")


def _scrape_custom(url: str) -> list[Job]:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()

    # Local demo board served by this app.
    if path.rstrip("/").endswith("/demo/board") or path.endswith("demo-board.html"):
        html = _fetch_text(url)
        return _parse_generic(BeautifulSoup(html, "lxml"), url)

    if "remoteok.com" in host:
        # RemoteOK HTML is a JS shell; use their public JSON feed.
        return _parse_remoteok_api(_fetch_bytes("https://remoteok.com/api"))

    if "remotive.com" in host and "/api/" in path:
        return _parse_remotive_api(_fetch_bytes(url))

    text_or_bytes, content_type = _fetch_payload(url)

    if "json" in content_type or url.rstrip("/").endswith(".json"):
        raw = text_or_bytes if isinstance(text_or_bytes, (bytes, bytearray)) else text_or_bytes.encode()
        return _parse_remoteok_api(raw)  # best-effort if shape matches

    if "xml" in content_type or "rss" in content_type or url.endswith(".rss"):
        raw = text_or_bytes if isinstance(text_or_bytes, (bytes, bytearray)) else text_or_bytes.encode()
        return _parse_rss(raw, url)

    html = (
        text_or_bytes.decode("utf-8", errors="replace")
        if isinstance(text_or_bytes, (bytes, bytearray))
        else text_or_bytes
    )
    soup = BeautifulSoup(html, "lxml")

    # Prefer linked RSS/JSON alternates when the page is a JS shell.
    for link in soup.select('link[rel="alternate"]'):
        href = link.get("href") or ""
        typ = (link.get("type") or "").lower()
        absolute = urljoin(url, href)
        if "rss" in typ or "xml" in typ or href.endswith(".rss"):
            return _parse_rss(_fetch_bytes(absolute), url)
        if "json" in typ:
            data = _fetch_bytes(absolute)
            jobs = _try_json_jobs(data)
            if jobs:
                return jobs

    if "weworkremotely.com" in host:
        jobs = _parse_weworkremotely(soup, url)
        if jobs:
            return jobs
        # Category pages may be empty in HTML; fall back to programming RSS.
        return _parse_rss(
            _fetch_bytes(
                "https://weworkremotely.com/categories/remote-programming-jobs.rss"
            ),
            url,
        )

    return _parse_generic(soup, url)


def _parse_remoteok_api(raw: bytes) -> list[Job]:
    data = json.loads(raw.decode("utf-8", errors="replace"))
    if not isinstance(data, list):
        raise ValueError("Unexpected RemoteOK payload")

    jobs: list[Job] = []
    for item in data:
        if not isinstance(item, dict):
            continue
        if "legal" in item and "position" not in item and "title" not in item:
            continue

        title = (item.get("position") or item.get("title") or "").strip()
        company = (item.get("company") or "").strip() or "Unknown company"
        if not title:
            continue

        tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
        if not _looks_frontend(title, tags):
            continue

        posted_at = _from_epoch(item.get("epoch")) or _parse_date_string(item.get("date"))
        location = (item.get("location") or "").strip() or "Remote"
        url = (item.get("url") or item.get("apply_url") or "").strip()
        if url and not url.startswith("http"):
            url = urljoin("https://remoteok.com", url)

        jobs.append(
            Job(
                title=title,
                company=company,
                location=location,
                url=url or "https://remoteok.com",
                posted_at=posted_at,
                posted_label=_format_posted(posted_at),
                tags=_unique(tags)[:8],
            )
        )
    return _dedupe_by_url(jobs)


def _parse_remotive_api(raw: bytes) -> list[Job]:
    data = json.loads(raw.decode("utf-8", errors="replace"))
    items = data.get("jobs") if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ValueError("Unexpected Remotive payload")

    jobs: list[Job] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        title = (item.get("title") or "").strip()
        if not title:
            continue
        tags = [str(tag).strip() for tag in (item.get("tags") or []) if str(tag).strip()]
        # Remotive often attaches broad tag dumps; prefer title signal.
        if not _looks_frontend(title, tags, title_preferred=True):
            continue
        company = (item.get("company_name") or item.get("company") or "").strip()
        location = (
            item.get("candidate_required_location")
            or item.get("location")
            or "Remote"
        )
        posted_at = _parse_date_string(item.get("publication_date") or item.get("date"))
        url = (item.get("url") or "").strip() or "https://remotive.com"

        jobs.append(
            Job(
                title=title,
                company=company or "Unknown company",
                location=str(location).strip() or "Remote",
                url=url,
                posted_at=posted_at,
                posted_label=_format_posted(posted_at),
                tags=_unique(tags)[:8],
            )
        )
    return _dedupe_by_url(jobs)


def _parse_rss(raw: bytes, source_url: str) -> list[Job]:
    soup = BeautifulSoup(raw, "lxml-xml")
    jobs: list[Job] = []
    for item in soup.find_all("item"):
        title_raw = _text(item.find("title"))
        if not title_raw:
            continue

        company, title = _split_wwr_title(title_raw)
        # Prefer front-end flavored roles when the feed is broader programming.
        if "programming" in source_url and not _looks_frontend(title_raw, []):
            # Keep full-stack / JS-heavy titles already covered by hints; skip clear backend-only.
            continue

        link = _text(item.find("link")) or ""
        posted_at = _parse_date_string(_text(item.find("pubDate")))
        region = _text(item.find("region")) or "Remote"

        jobs.append(
            Job(
                title=title,
                company=company,
                location=region,
                url=urljoin(source_url, link) if link else source_url,
                posted_at=posted_at,
                posted_label=_format_posted(posted_at),
                tags=[],
            )
        )

    # If the front-end filter emptied a programming feed, show the raw feed instead.
    if not jobs:
        for item in soup.find_all("item"):
            title_raw = _text(item.find("title"))
            if not title_raw:
                continue
            company, title = _split_wwr_title(title_raw)
            link = _text(item.find("link")) or ""
            posted_at = _parse_date_string(_text(item.find("pubDate")))
            jobs.append(
                Job(
                    title=title,
                    company=company,
                    location=_text(item.find("region")) or "Remote",
                    url=urljoin(source_url, link) if link else source_url,
                    posted_at=posted_at,
                    posted_label=_format_posted(posted_at),
                    tags=[],
                )
            )
    return _dedupe_by_url(jobs)


def _parse_weworkremotely(soup: BeautifulSoup, source_url: str) -> list[Job]:
    jobs: list[Job] = []
    for li in soup.select("li"):
        link = li.select_one("a[href*='/remote-jobs/']")
        if not link:
            continue
        href = link.get("href") or ""
        if "/remote-jobs/find" in href or "utm_source" in href:
            continue

        title = (
            _text(li.select_one(".title"))
            or _text(link.select_one(".title"))
            or _text(link)
        )
        company = (
            _text(li.select_one(".company"))
            or _text(link.select_one(".company"))
            or "Unknown company"
        )
        if not title or len(title) < 2 or title.lower() == "post a job":
            continue

        region = (
            _text(li.select_one(".region"))
            or _text(link.select_one(".region"))
            or "Remote"
        )
        date_el = li.select_one("time") or li.select_one(".date")
        posted_raw = (
            date_el.get("datetime")
            if date_el is not None and date_el.has_attr("datetime")
            else _text(date_el)
        )
        posted_at = _parse_date_string(posted_raw)

        jobs.append(
            Job(
                title=title,
                company=company,
                location=region,
                url=urljoin(source_url, href),
                posted_at=posted_at,
                posted_label=_format_posted(posted_at),
                tags=[],
            )
        )
    return _dedupe_by_url(jobs)


def _parse_generic(soup: BeautifulSoup, source_url: str) -> list[Job]:
    jobs: list[Job] = []
    candidates = soup.select(
        "article.job, article, li.job, li.listing, div.job, div.listing, "
        "tr.job:not(.placeholder), [class*='job-card'], [class*='job_listing'], "
        "[data-job]"
    )
    for node in candidates:
        link = node.select_one("a[href]")
        if not link:
            continue
        title = (
            _text(node.select_one("h2, h3, h4, .title, [class*='title']"))
            or _text(link)
        )
        if not title or len(title) < 3:
            continue
        if title.lower() in {"post a job", "view all", "see more"}:
            continue

        company = _text(
            node.select_one(
                ".company, [class*='company'], [class*='employer'], [data-company]"
            )
        ) or "Unknown company"
        location = _text(
            node.select_one(".location, [class*='location'], [data-location]")
        ) or "—"
        date_el = node.select_one("time, .date, [class*='date'], [class*='posted']")
        posted_raw = (
            date_el.get("datetime")
            if date_el is not None and date_el.has_attr("datetime")
            else _text(date_el)
        )
        posted_at = _parse_date_string(posted_raw)
        tags = [
            _text(tag)
            for tag in node.select(".tag, .badge, [class*='tag']")
            if _text(tag) and len(_text(tag)) < 32
        ]

        jobs.append(
            Job(
                title=title,
                company=company,
                location=location,
                url=urljoin(source_url, link.get("href") or ""),
                posted_at=posted_at,
                posted_label=_format_posted(posted_at),
                tags=_unique(tags)[:6],
            )
        )
    return _dedupe_by_url(jobs)[:80]


def _try_json_jobs(raw: bytes) -> list[Job]:
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []
    if isinstance(data, list):
        return _parse_remoteok_api(raw)
    if isinstance(data, dict) and "jobs" in data:
        return _parse_remotive_api(raw)
    return []


def _looks_frontend(
    title: str, tags: list[str], *, title_preferred: bool = False
) -> bool:
    title_l = title.lower()
    title_hit = any(hint in title_l for hint in FRONTEND_TITLE_HINTS)
    if title_preferred:
        return title_hit
    tag_l = {tag.lower() for tag in tags}
    if tag_l & FRONTEND_TAG_HINTS:
        return True
    return title_hit


def _split_wwr_title(title_raw: str) -> tuple[str, str]:
    if ":" in title_raw:
        company, title = title_raw.split(":", 1)
        return company.strip() or "Unknown company", title.strip() or title_raw
    return "Unknown company", title_raw


def _normalize_days(days: int) -> int:
    try:
        value = int(days)
    except (TypeError, ValueError):
        return 7
    if value in DAY_OPTIONS:
        return value
    return min(DAY_OPTIONS, key=lambda option: abs(option - value))


def _validate_url(url: str) -> tuple[bool, Optional[str]]:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://."
    if not parsed.netloc:
        return False, "That does not look like a valid URL."
    return True, None


def _fetch_bytes(url: str) -> bytes:
    payload, _ = _fetch_payload(url)
    if isinstance(payload, str):
        return payload.encode("utf-8")
    return payload


def _fetch_text(url: str) -> str:
    payload, _ = _fetch_payload(url)
    if isinstance(payload, str):
        return payload
    return payload.decode("utf-8", errors="replace")


def _fetch_payload(url: str) -> tuple[bytes | str, str]:
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            response = requests.get(
                url,
                headers={
                    "User-Agent": USER_AGENT,
                    "Accept": (
                        "text/html,application/xhtml+xml,application/json,"
                        "application/rss+xml;q=0.9,*/*;q=0.8"
                    ),
                    "Accept-Language": "en-US,en;q=0.9",
                },
                timeout=REQUEST_TIMEOUT,
                stream=True,
            )
            response.raise_for_status()
            content_type = (response.headers.get("Content-Type") or "").lower()

            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_content(chunk_size=64_000):
                if not chunk:
                    continue
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise requests.RequestException("Response is too large to parse.")
                chunks.append(chunk)
            return b"".join(chunks), content_type
        except (requests.Timeout, requests.ConnectionError) as exc:
            last_error = exc
            if attempt == 0:
                continue
            raise
    assert last_error is not None
    raise last_error


def _filter_by_days(jobs: list[Job], days: int) -> tuple[list[Job], int]:
    cutoff = datetime.now(timezone.utc) - relativedelta(days=days)
    kept: list[Job] = []
    dropped = 0

    for job in jobs:
        if job.posted_at is None:
            kept.append(job)
            continue
        posted = job.posted_at
        if posted.tzinfo is None:
            posted = posted.replace(tzinfo=timezone.utc)
        if posted >= cutoff:
            kept.append(job)
        else:
            dropped += 1

    kept.sort(
        key=lambda job: job.posted_at or datetime.min.replace(tzinfo=timezone.utc),
        reverse=True,
    )
    return kept, dropped


def _from_epoch(value) -> Optional[datetime]:
    if value is None or value == "":
        return None
    try:
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _parse_date_string(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    text = str(value).strip()
    if not text:
        return None

    lowered = text.lower()
    now = datetime.now(timezone.utc)
    if lowered in ("just now", "today", "now"):
        return now
    if lowered == "yesterday":
        return now - relativedelta(days=1)

    relative = _parse_relative(lowered, now)
    if relative is not None:
        return relative

    try:
        parsed = date_parser.parse(text, fuzzy=True)
    except (ValueError, OverflowError, TypeError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def _parse_relative(text: str, now: datetime) -> Optional[datetime]:
    match = re.match(
        r"(?:posted\s+)?(\d+)\s*(m|min|mins|minute|minutes|h|hr|hrs|hour|hours|"
        r"d|day|days|w|wk|wks|week|weeks|mo|month|months)\s*(?:ago)?",
        text,
    )
    if not match:
        return None

    amount = int(match.group(1))
    unit = match.group(2)
    if unit.startswith("m") and unit not in ("mo", "month", "months"):
        return now - relativedelta(minutes=amount)
    if unit.startswith("h"):
        return now - relativedelta(hours=amount)
    if unit.startswith("d"):
        return now - relativedelta(days=amount)
    if unit.startswith("w"):
        return now - relativedelta(weeks=amount)
    if unit.startswith("mo") or unit.startswith("month"):
        return now - relativedelta(months=amount)
    return None


def _format_posted(posted_at: Optional[datetime]) -> str:
    if posted_at is None:
        return "Date unknown"
    posted = posted_at
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=timezone.utc)
    delta = datetime.now(timezone.utc) - posted
    seconds = int(delta.total_seconds())
    if seconds < 0:
        return "Just posted"
    if seconds < 3600:
        mins = max(1, seconds // 60)
        return f"{mins}m ago"
    if seconds < 86400:
        hours = seconds // 3600
        return f"{hours}h ago"
    days = seconds // 86400
    if days == 1:
        return "1 day ago"
    if days < 30:
        return f"{days} days ago"
    return posted.strftime("%b %d, %Y")


def _text(node) -> str:
    if node is None:
        return ""
    return " ".join(node.get_text(" ", strip=True).split())


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for value in values:
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _dedupe_by_url(jobs: list[Job]) -> list[Job]:
    seen: set[str] = set()
    out: list[Job] = []
    for job in jobs:
        if job.url in seen:
            continue
        seen.add(job.url)
        out.append(job)
    return out
