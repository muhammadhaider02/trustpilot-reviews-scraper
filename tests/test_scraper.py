import json
import re
from pathlib import Path

import pytest

from trustpilot_reviews import scraper
from trustpilot_reviews.scraper import (
    NoTrustpilotPage,
    ScrapeBlocked,
    build_url,
    clean_domain,
    date_window,
    extract_next_data,
    parse_page,
    scrape,
)

FIXTURES = Path(__file__).parent / "fixtures"

# Copied verbatim from Stage 4's `Parse Report` node. Brand-side errors must match; vendor-side must not.
BRAND_ERROR_RE = re.compile(r"\b404\b|not found|no such (company|business|page)|invalid (url|domain)", re.I)


def fixture_html(name: str) -> str:
    data = (FIXTURES / name).read_text(encoding="utf-8")
    return f'<html><head><title>x</title></head><body><script id="__NEXT_DATA__" type="application/json">{data}</script></body></html>'


@pytest.fixture(autouse=True)
def no_sleep(monkeypatch):
    monkeypatch.setattr(scraper.time, "sleep", lambda *_: None)


# ------------------------------------------------------------------ url helpers


@pytest.mark.parametrize(
    "raw, expected",
    [
        ("gymshark.com", "gymshark.com"),
        ("https://www.gymshark.com/", "gymshark.com"),
        ("HTTP://Gymshark.COM/collections?x=1", "gymshark.com"),
        ("  nobltravel.com  ", "nobltravel.com"),
    ],
)
def test_clean_domain(raw, expected):
    assert clean_domain(raw) == expected


@pytest.mark.parametrize("raw", ["", "gymshark", "https://", "/"])
def test_clean_domain_rejects_garbage(raw):
    with pytest.raises(ValueError, match="invalid domain"):
        clean_domain(raw)


@pytest.mark.parametrize(
    "months, expected",
    [(1, "last30days"), (2, "last3months"), (3, "last3months"), (6, "last6months"), (12, "last12months"), (24, None), (0, None), (None, None)],
)
def test_date_window(months, expected):
    assert date_window(months) == expected


def test_build_url_matches_trustpilot_filter_params():
    url = build_url("gymshark.com", [2, 1], 12)
    assert url.startswith("https://www.trustpilot.com/review/gymshark.com?")
    assert "stars=1&stars=2" in url
    assert "sort=recency" in url
    assert "date=last12months" in url
    assert "page=" not in url
    assert "page=3" in build_url("gymshark.com", [3], 12, page=3)


# ------------------------------------------------------------------ parsing


def test_parse_fixture_page():
    data = json.loads((FIXTURES / "gymshark_neg_page1.json").read_text(encoding="utf-8"))
    page = parse_page(data)
    assert len(page.reviews) == 20
    assert {r.rating for r in page.reviews} <= {1, 2}
    assert page.total_pages > 1 and page.total_count > 20
    assert page.business_unit.display_name == "Gymshark"
    assert page.business_unit.identifying_name == "gymshark.com"
    assert page.business_unit.trust_score == 3.5
    assert page.business_unit.number_of_reviews > 40000
    r = page.reviews[0]
    assert r.id and r.text and r.title and r.published_date.endswith("Z")
    assert r.consumer_country
    assert page.selected_filters["stars"] == [1, 2]


def test_extract_next_data_absent():
    assert extract_next_data("<html><body>Verifying your connection</body></html>") is None
    assert extract_next_data('<script id="__NEXT_DATA__" type="application/json">{not json</script>') is None


# ------------------------------------------------------------------ scrape() control flow


def test_scrape_happy_path_single_page():
    calls = []

    def fetcher(url):
        calls.append(url)
        return 200, fixture_html("gymshark_neg_page1.json")

    res = scrape("gymshark.com", [1, 2], max_reviews=20, months=12, fetcher=fetcher)
    assert len(calls) == 1
    assert len(res.reviews) == 20
    assert res.pages_fetched == 1
    assert res.business_unit.trust_score == 3.5
    assert res.domain == "gymshark.com"


def test_scrape_paginates_until_max():
    base = json.loads((FIXTURES / "gymshark_neg_page1.json").read_text(encoding="utf-8"))
    page2 = json.loads(json.dumps(base))
    for r in page2["props"]["pageProps"]["reviews"]:
        r["id"] = "p2-" + r["id"]
    page2["props"]["pageProps"]["filters"]["pagination"]["currentPage"] = 2
    calls = []

    def fetcher(url):
        calls.append(url)
        data = page2 if "page=2" in url else base
        return 200, f'<script id="__NEXT_DATA__" type="application/json">{json.dumps(data)}</script>'

    res = scrape("gymshark.com", [1, 2], max_reviews=30, months=12, fetcher=fetcher)
    assert len(calls) == 2
    assert len(res.reviews) == 30
    assert res.pages_fetched == 2
    assert len({r.id for r in res.reviews}) == 30


def test_scrape_dedupes_repeated_page():
    def fetcher(url):
        return 200, fixture_html("gymshark_neg_page1.json")

    # Page 2 returns identical ids to page 1, so nothing new should be added.
    res = scrape("gymshark.com", [1, 2], max_reviews=40, months=12, fetcher=fetcher)
    assert len(res.reviews) == 20


def test_scrape_404_is_brand_error():
    def fetcher(url):
        return 404, "<html><title>Not found</title></html>"

    with pytest.raises(NoTrustpilotPage) as ei:
        scrape("no-such-brand-zz9q.com", [1, 2], fetcher=fetcher)
    assert ei.value.status == 404
    assert BRAND_ERROR_RE.search(str(ei.value)), "Stage 4 must classify this as the brand's problem"


def test_scrape_block_is_vendor_error_and_retries_once():
    calls = []

    def fetcher(url):
        calls.append(url)
        return 403, "<html><title>Verifying your connection</title></html>"

    with pytest.raises(ScrapeBlocked) as ei:
        scrape("gymshark.com", [1, 2], fetcher=fetcher)
    assert len(calls) == 2
    assert ei.value.status == 503
    assert not BRAND_ERROR_RE.search(str(ei.value)), "a block must NOT burn one of the brand's retries in Stage 4"


def test_scrape_missing_next_data_is_vendor_error():
    def fetcher(url):
        return 200, "<html><title>Please wait while we verify your browser</title></html>"

    with pytest.raises(ScrapeBlocked) as ei:
        scrape("gymshark.com", [1, 2], fetcher=fetcher)
    assert not BRAND_ERROR_RE.search(str(ei.value))


def test_scrape_recovers_when_retry_succeeds():
    calls = []

    def fetcher(url):
        calls.append(url)
        if len(calls) == 1:
            return 429, "<html><title>slow down</title></html>"
        return 200, fixture_html("gymshark_neg_page1.json")

    res = scrape("gymshark.com", [1, 2], fetcher=fetcher)
    assert len(calls) == 2
    assert len(res.reviews) == 20
