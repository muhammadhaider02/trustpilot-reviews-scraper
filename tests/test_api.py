import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from trustpilot_reviews import api
from trustpilot_reviews.scraper import NoTrustpilotPage, ScrapeBlocked, parse_page

FIXTURES = Path(__file__).parent / "fixtures"

# The exact field lookups Stage 4's `Sort Trustpilot Reviews` node performs on each Apify item.
SORT_NODE_FIELDS = {"rating", "text", "title", "publishedDate", "country", "reviewId", "companyUrl", "companyName", "companyTrustScore", "companyTotalReviews"}


def canned_result(domain="gymshark.com"):
    from trustpilot_reviews.scraper import ScrapeResult

    page = parse_page(json.loads((FIXTURES / "gymshark_neg_page1.json").read_text(encoding="utf-8")))
    return ScrapeResult(
        domain=domain,
        reviews=page.reviews,
        business_unit=page.business_unit,
        pages_fetched=1,
        total_pages=page.total_pages,
        total_count=page.total_count,
        seconds=1.5,
        url="https://www.trustpilot.com/review/" + domain,
    )


def _set_frozen(obj, name, value):
    """Settings is a frozen dataclass; tests poke it directly."""
    object.__setattr__(obj, name, value)


@pytest.fixture
def client():
    _set_frozen(api.settings, "api_token", "")
    with TestClient(api.app) as c:
        yield c


def test_post_returns_apify_shaped_items(client, monkeypatch):
    seen = {}

    def fake_scrape(domain, stars, max_reviews, months):
        seen.update(domain=domain, stars=stars, max_reviews=max_reviews, months=months)
        return canned_result(domain)

    monkeypatch.setattr(api, "scrape", fake_scrape)
    r = client.post("/trustpilot", json={"domain": "gymshark.com", "stars": ["1", "2"], "max": 30, "months": 12})
    assert r.status_code == 200
    items = r.json()
    assert isinstance(items, list) and len(items) == 20
    assert SORT_NODE_FIELDS <= set(items[0])
    assert items[0]["companyTrustScore"] == 3.5
    assert items[0]["companyUrl"] == "https://www.trustpilot.com/review/gymshark.com"
    assert seen == {"domain": "gymshark.com", "stars": [1, 2], "max_reviews": 30, "months": 12}
    assert r.headers["X-Pages-Fetched"] == "1"
    assert r.headers["X-Truncated"] == "false"


def test_truncated_result_is_flagged_in_the_headers(client, monkeypatch):
    """A budget-truncated scrape is a 200 with real reviews, so the header is the only way the
    caller can tell it apart from 'that is all the reviews there were'."""
    import dataclasses

    def fake_scrape(*a, **k):
        return dataclasses.replace(canned_result(), truncated=True)

    monkeypatch.setattr(api, "scrape", fake_scrape)
    r = client.post("/trustpilot", json={"domain": "gymshark.com"})
    assert r.status_code == 200
    assert len(r.json()) == 20, "truncated still returns the reviews it did collect"
    assert r.headers["X-Truncated"] == "true"


def test_post_accepts_apify_body_verbatim(client, monkeypatch):
    """The body Stage 4 sends Apify today should work unchanged, so the n8n edit is just the URL."""
    seen = {}

    def fake_scrape(domain, stars, max_reviews, months):
        seen.update(domain=domain, stars=stars, max_reviews=max_reviews, months=months)
        return canned_result(domain)

    monkeypatch.setattr(api, "scrape", fake_scrape)
    body = {"companyUrls": ["gymshark.com"], "stars": ["1", "2"], "maxReviewsPerCompany": 30, "sort": "recency", "date": "last12months", "includeCompanyInfo": True}
    r = client.post("/trustpilot", json=body)
    assert r.status_code == 200
    assert seen == {"domain": "gymshark.com", "stars": [1, 2], "max_reviews": 30, "months": 12}


def test_missing_page_is_an_empty_array_not_an_error(client, monkeypatch):
    # Apify returned an empty dataset for a domain with no Trustpilot page and Stage 4 read that as
    # `no_results`; a 404 error item read as `request_failed` and spent one of the brand's retries.
    def fake_scrape(*a, **k):
        raise NoTrustpilotPage("404 not found: no Trustpilot page at https://www.trustpilot.com/review/x.com")

    monkeypatch.setattr(api, "scrape", fake_scrape)
    before = api.counters["not_found"]
    r = client.post("/trustpilot", json={"domain": "x.com", "stars": [1, 2]})
    assert r.status_code == 200
    assert r.json() == []
    assert r.headers["X-No-Trustpilot-Page"] == "true"
    assert api.counters["not_found"] == before + 1


def test_post_503_when_blocked(client, monkeypatch):
    def fake_scrape(*a, **k):
        raise ScrapeBlocked("trustpilot answered HTTP 403 (bot challenge or rate limit)")

    monkeypatch.setattr(api, "scrape", fake_scrape)
    r = client.post("/trustpilot", json={"domain": "gymshark.com"})
    assert r.status_code == 503
    assert r.json()["error"]["type"] == "ScrapeBlocked"


def test_post_400_without_domain(client):
    r = client.post("/trustpilot", json={"stars": [1]})
    assert r.status_code == 400
    assert "invalid domain" in r.json()["error"]["message"]


def test_bearer_token_enforced(monkeypatch):
    _set_frozen(api.settings, "api_token", "s3cret")
    try:
        monkeypatch.setattr(api, "scrape", lambda *a, **k: canned_result())
        with TestClient(api.app) as c:
            assert c.post("/trustpilot", json={"domain": "gymshark.com"}).status_code == 401
            assert c.post("/trustpilot", json={"domain": "gymshark.com"}, headers={"Authorization": "Bearer nope"}).status_code == 401
            assert c.post("/trustpilot", json={"domain": "gymshark.com"}, headers={"Authorization": "Bearer s3cret"}).status_code == 200
            assert c.get("/health").status_code == 200  # health stays open for uptime checks
    finally:
        _set_frozen(api.settings, "api_token", "")


def test_health_counters(client, monkeypatch):
    monkeypatch.setattr(api, "scrape", lambda *a, **k: canned_result())
    before = client.get("/health").json()
    client.post("/trustpilot", json={"domain": "gymshark.com"})
    after = client.get("/health").json()
    assert after["ok"] == before["ok"] + 1
    assert after["in_flight"] == 0
