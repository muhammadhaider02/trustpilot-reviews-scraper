"""Fetch Trustpilot review pages with a stealth browser and parse the embedded __NEXT_DATA__ JSON.

Trustpilot is a Next.js site: every review page carries a <script id="__NEXT_DATA__"> blob with the
same structured data the page renders. Reading that blob is far more stable than CSS selectors.
The site sits behind a CloudFront JS challenge that blocks plain HTTP clients (curl, httpx, even
TLS-impersonating ones), so a real headless browser via Scrapling is required.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Callable
from urllib.parse import urlencode

from .config import settings

log = logging.getLogger(__name__)

BASE = "https://www.trustpilot.com/review/"
PER_PAGE = 20
NEXT_DATA_RE = re.compile(r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S)

# Trustpilot only offers these fixed date windows. A requested month count maps to the smallest
# window that covers it; anything larger than 12 means "all time".
DATE_WINDOWS = [(1, "last30days"), (3, "last3months"), (6, "last6months"), (12, "last12months")]


class TrustpilotError(Exception):
    """Base class. `status` is the HTTP status the API should answer with."""

    status = 502


class NoTrustpilotPage(TrustpilotError):
    """Brand has no Trustpilot profile. This is the BRAND's problem, so Stage 4 should burn a retry.
    The message must contain '404' / 'not found' to match Stage 4's BRAND_ERROR_RE."""

    status = 404


class ScrapeBlocked(TrustpilotError):
    """We were challenged or served a non-review page. This is OUR problem (vendor failure in Stage 4
    terms), so the message must NOT contain '404', 'not found', 'no such page' or 'invalid url'."""

    status = 503


class ScrapeFailed(TrustpilotError):
    """Browser or network failure. Also a vendor failure from Stage 4's point of view."""

    status = 503


@dataclass
class BusinessUnit:
    display_name: str = ""
    identifying_name: str = ""
    trust_score: float | None = None
    stars: float | None = None
    number_of_reviews: int | None = None
    number_of_reviews_last_12_months: int | None = None
    website_url: str = ""
    country_code: str = ""
    is_closed: bool = False
    is_claimed: bool = False


@dataclass
class Review:
    id: str
    rating: int
    title: str
    text: str
    language: str
    published_date: str
    experienced_date: str
    updated_date: str
    consumer_name: str
    consumer_country: str
    consumer_review_count: int
    verified: bool
    verification_source: str
    likes: int
    reply_message: str
    reply_date: str


@dataclass
class PageData:
    reviews: list[Review]
    business_unit: BusinessUnit
    current_page: int
    total_pages: int
    total_count: int
    selected_filters: dict


@dataclass
class ScrapeResult:
    domain: str
    reviews: list[Review]
    business_unit: BusinessUnit
    pages_fetched: int
    total_pages: int
    total_count: int
    seconds: float
    url: str
    selected_filters: dict = field(default_factory=dict)


# --------------------------------------------------------------------------- URL


def clean_domain(raw: str) -> str:
    d = raw.strip().lower()
    d = re.sub(r"^https?://", "", d)
    d = re.sub(r"^www\.", "", d)
    d = d.split("/")[0].split("?")[0]
    if not d or "." not in d:
        raise ValueError(f"invalid domain: {raw!r}")
    return d


def date_window(months: int | None) -> str | None:
    if months is None or months <= 0:
        return None
    for limit, key in DATE_WINDOWS:
        if months <= limit:
            return key
    return None


def build_url(domain: str, stars: list[int], months: int | None, page: int = 1, languages: str = "en") -> str:
    params: list[tuple[str, str]] = [("stars", str(s)) for s in sorted(set(stars))]
    params.append(("sort", "recency"))
    params.append(("languages", languages))
    window = date_window(months)
    if window:
        params.append(("date", window))
    if page > 1:
        params.append(("page", str(page)))
    return BASE + domain + "?" + urlencode(params)


# --------------------------------------------------------------------------- parsing


def extract_next_data(html: str) -> dict | None:
    m = NEXT_DATA_RE.search(html)
    if not m:
        return None
    try:
        return json.loads(m.group(1))
    except json.JSONDecodeError:
        return None


def _s(v) -> str:
    return "" if v is None else str(v)


def parse_business_unit(pp: dict) -> BusinessUnit:
    bu = pp.get("businessUnit") or {}
    return BusinessUnit(
        display_name=_s(bu.get("displayName")),
        identifying_name=_s(bu.get("identifyingName")),
        trust_score=bu.get("trustScore"),
        stars=bu.get("stars"),
        number_of_reviews=bu.get("numberOfReviews"),
        number_of_reviews_last_12_months=bu.get("numberOfReviewsLast12Months"),
        website_url=_s(bu.get("websiteUrl")),
        country_code=_s(bu.get("countryCode")),
        is_closed=bool(bu.get("isClosed")),
        is_claimed=bool(bu.get("isClaimed")),
    )


def parse_review(r: dict) -> Review:
    dates = r.get("dates") or {}
    consumer = r.get("consumer") or {}
    reply = r.get("reply") or {}
    verification = (r.get("labels") or {}).get("verification") or {}
    return Review(
        id=_s(r.get("id")),
        rating=int(r.get("rating") or 0),
        title=_s(r.get("title")).strip(),
        text=_s(r.get("text")).strip(),
        language=_s(r.get("language")),
        published_date=_s(dates.get("publishedDate")),
        experienced_date=_s(dates.get("experiencedDate")),
        updated_date=_s(dates.get("updatedDate")),
        consumer_name=_s(consumer.get("displayName")),
        consumer_country=_s(consumer.get("countryCode")),
        consumer_review_count=int(consumer.get("numberOfReviews") or 0),
        verified=bool(verification.get("isVerified")),
        verification_source=_s(verification.get("verificationSource")),
        likes=int(r.get("likes") or 0),
        reply_message=_s(reply.get("message")),
        reply_date=_s(reply.get("publishedDate")),
    )


def parse_page(next_data: dict) -> PageData:
    pp = (next_data.get("props") or {}).get("pageProps") or {}
    filters = pp.get("filters") or {}
    pagination = filters.get("pagination") or {}
    return PageData(
        reviews=[parse_review(r) for r in (pp.get("reviews") or [])],
        business_unit=parse_business_unit(pp),
        current_page=int(pagination.get("currentPage") or 1),
        total_pages=int(pagination.get("totalPages") or 1),
        total_count=int(pagination.get("totalCount") or 0),
        selected_filters=filters.get("selected") or {},
    )


# --------------------------------------------------------------------------- fetching


def _html_of(page) -> str:
    for attr in ("html_content", "body", "text"):
        v = getattr(page, attr, None)
        if v:
            return v.decode("utf-8", "replace") if isinstance(v, bytes) else str(v)
    return ""


def fetch_html(url: str) -> tuple[int, str]:
    """One stealth-browser fetch. Returns (status, html). Raises ScrapeFailed on browser/network errors."""
    from scrapling.fetchers import StealthyFetcher

    try:
        page = StealthyFetcher.fetch(
            url,
            headless=True,
            network_idle=True,
            disable_resources=settings.block_resources,
            solve_cloudflare=settings.solve_cloudflare,
            humanize=True,
            block_webrtc=True,
            os_randomize=True,
            geoip=True,
            proxy=settings.proxy,
            timeout=settings.fetch_timeout_ms,
        )
    except Exception as e:  # noqa: BLE001 - anything from the browser stack is a vendor failure
        raise ScrapeFailed(f"browser fetch failed: {type(e).__name__}: {e}"[:300]) from e
    return int(page.status), _html_of(page)


def _title_of(html: str) -> str:
    m = re.search(r"<title>(.*?)</title>", html, re.S)
    return re.sub(r"\s+", " ", m.group(1)).strip()[:120] if m else ""


def fetch_page(url: str, fetcher: Callable[[str], tuple[int, str]] = fetch_html, attempts: int = 2) -> PageData:
    """Fetch and parse one review page, retrying once on a challenge/non-200 (but not on 404)."""
    last_err: TrustpilotError | None = None
    for attempt in range(1, attempts + 1):
        status, html = fetcher(url)
        if status == 404:
            raise NoTrustpilotPage(f"404 not found: no Trustpilot page at {url}")
        if status != 200:
            last_err = ScrapeBlocked(
                f"trustpilot answered HTTP {status} (bot challenge or rate limit), title={_title_of(html)!r}"
            )
        else:
            data = extract_next_data(html)
            if data is None:
                last_err = ScrapeBlocked(
                    f"trustpilot served a page without __NEXT_DATA__ (challenge interstitial?), title={_title_of(html)!r}"
                )
            else:
                return parse_page(data)
        log.warning("attempt %d/%d failed for %s: %s", attempt, attempts, url, last_err)
        if attempt < attempts:
            time.sleep(settings.page_delay_s)
    assert last_err is not None
    raise last_err


def scrape(
    domain: str,
    stars: list[int],
    max_reviews: int = PER_PAGE,
    months: int | None = 12,
    fetcher: Callable[[str], tuple[int, str]] = fetch_html,
) -> ScrapeResult:
    """Collect up to `max_reviews` most-recent reviews in the given star band, newest first."""
    domain = clean_domain(domain)
    stars = [int(s) for s in stars if 1 <= int(s) <= 5] or [1, 2, 3, 4, 5]
    max_reviews = max(1, int(max_reviews))
    started = time.time()

    cutoff = datetime.now(timezone.utc) - timedelta(days=30 * months) if months and months > 0 else None

    seen: set[str] = set()
    reviews: list[Review] = []
    bu = BusinessUnit()
    total_pages = 1
    total_count = 0
    selected: dict = {}
    page_no = 0
    first_url = build_url(domain, stars, months, 1)
    stop = False

    while not stop:
        page_no += 1
        url = first_url if page_no == 1 else build_url(domain, stars, months, page_no)
        if page_no > 1:
            time.sleep(settings.page_delay_s)
        data = fetch_page(url, fetcher=fetcher)

        if page_no == 1:
            bu = data.business_unit
            total_pages = data.total_pages
            total_count = data.total_count
            selected = data.selected_filters
            if bu.is_closed:
                log.info("%s is marked closed on Trustpilot", domain)

        for r in data.reviews:
            if r.rating not in stars:
                continue  # belt and braces: the server-side filter has always held so far
            if cutoff and r.published_date:
                try:
                    published = datetime.fromisoformat(r.published_date.replace("Z", "+00:00"))
                    if published < cutoff:
                        stop = True  # sorted by recency, so nothing after this is newer
                        break
                except ValueError:
                    pass
            key = r.id or f"{r.consumer_name}|{r.title}|{r.published_date}"
            if key in seen:
                continue
            seen.add(key)
            reviews.append(r)
            if len(reviews) >= max_reviews:
                stop = True
                break

        if not data.reviews or page_no >= total_pages or page_no >= settings.max_pages:
            stop = True

    return ScrapeResult(
        domain=domain,
        reviews=reviews[:max_reviews],
        business_unit=bu,
        pages_fetched=page_no,
        total_pages=total_pages,
        total_count=total_count,
        seconds=round(time.time() - started, 1),
        url=first_url,
        selected_filters=selected,
    )
