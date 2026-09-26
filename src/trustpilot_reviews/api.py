"""HTTP surface the caller uses in place of Apify's run-sync-get-dataset-items endpoint.

POST /trustpilot  -> JSON array of review items (same names the caller's Sort node reads from Apify)
GET  /health      -> counters, useful for uptime and health checks

A 200 may be a partial result: if the scrape hits SCRAPE_BUDGET_S it returns the reviews it
already collected and sets `X-Truncated: true`. The body looks identical to a complete run.

Error contract, matched to the caller's retry logic:
  200 []                brand has no Trustpilot page -> the caller reads `no_results`, as it did
                        from Apify's empty dataset; `X-No-Trustpilot-Page: true` says why
  400 {"error": {...}}  invalid domain               -> brand-side, burns one of its retries
  503 {"error": {...}}  we were blocked / browser died -> vendor failure, no retry burned
"""

from __future__ import annotations

import asyncio
import logging
import time
from contextlib import asynccontextmanager
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from pydantic import AliasChoices, BaseModel, ConfigDict, Field, field_validator

from . import __version__
from .config import settings
from .mapping import to_items
from .scraper import NoTrustpilotPage, TrustpilotError, scrape

log = logging.getLogger("trustpilot_reviews.api")

counters = {"requests": 0, "ok": 0, "empty": 0, "truncated": 0, "not_found": 0, "blocked": 0, "failed": 0, "in_flight": 0}
_semaphore: asyncio.Semaphore | None = None


@asynccontextmanager
async def lifespan(_: FastAPI):
    global _semaphore
    _semaphore = asyncio.Semaphore(settings.max_concurrency)
    if not settings.api_token:
        log.warning("API_TOKEN is not set - the scraper endpoint is unauthenticated")
    yield


app = FastAPI(title="trustpilot-reviews", version=__version__, lifespan=lifespan)


class ScrapeRequest(BaseModel):
    """Accepts both our own field names and the Apify body the caller already sends, so switching
    over can be as small as swapping the URL."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    domain: str | None = Field(default=None, validation_alias=AliasChoices("domain", "domain_clean"))
    company_urls: list[str] | None = Field(default=None, validation_alias=AliasChoices("companyUrls", "company_urls"))
    stars: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    max_reviews: int = Field(default=20, ge=1, le=100, validation_alias=AliasChoices("max", "maxReviewsPerCompany", "max_reviews"))
    months: int | None = Field(default=None, ge=1)
    # Apify's `date` preset is accepted and IGNORED, on purpose. Until 21 Sep 2026 `last12months`
    # became months=12, applied as Trustpilot's own window and as a cut-off. That was faithful to
    # the field and unfaithful to the actor's output, which is not windowed consistently. Measured
    # against Trustpilot's own counts on 21 Sep 2026 -
    #   fieldcrestproducts.com  14 reviews, 0 in the last 12 months; Apify total 14, kept 8 (the 8
    #                           of 14 that are under 3 years old, which is the caller's own filter)
    #   apexlift.com            3 reviews, 0 in the window; Apify total 3, kept 2 (the 2 under 3 years)
    #   quarrytech.com          1 review from 2021, 0 in the window; Apify score 3.2, all_irrelevant
    #   talon9.com              7287 reviews, 2 in the window; Apify kept 11 = exactly the 11 under
    #                           3 years, and no 12-month window at any date holds more than 5 of them
    #   fieldcrest.com          37 reviews, 24 in the window; Apify kept 21 = the window, all-time is 32
    #   ridgelinesports.com     584 reviews; Apify kept 29 = the window, all-time is 42
    # So honouring the window returned 0 / 0 / 0 / 2 rows for the first four against a baseline
    # that had reviews for all of them, while the last two say the actor did window sometimes.
    # No single rule reproduces both; ignoring the window is the one that is never below the
    # baseline, and age is the caller's job anyway (1,095 days on `publishedDate`). An explicit
    # integer `months` still works for anyone who wants a window.
    date: str | None = None
    include_company_info: bool = Field(default=True, validation_alias=AliasChoices("includeCompanyInfo", "include_company_info"))

    @field_validator("stars", mode="before")
    @classmethod
    def _coerce_stars(cls, v):
        if v is None:
            return [1, 2, 3, 4, 5]
        if isinstance(v, (str, int)):
            v = str(v).split(",")
        out = []
        for s in v:
            try:
                n = int(str(s).strip())
            except ValueError:
                continue
            if 1 <= n <= 5:
                out.append(n)
        return out or [1, 2, 3, 4, 5]

    def target_domain(self) -> str:
        if self.domain:
            return self.domain
        if self.company_urls:
            return self.company_urls[0]
        raise ValueError("invalid domain: request must include `domain` (or `companyUrls`)")


def require_token(authorization: Annotated[str | None, Header()] = None) -> None:
    if not settings.api_token:
        return
    expected = f"Bearer {settings.api_token}"
    if authorization != expected:
        raise HTTPException(status_code=401, detail="invalid or missing bearer token")


def _error_body(exc: Exception, status: int) -> dict:
    return {"error": {"type": type(exc).__name__, "status": status, "message": str(exc), "description": str(exc)}}


@app.post("/trustpilot", dependencies=[Depends(require_token)])
async def trustpilot(req: ScrapeRequest):
    assert _semaphore is not None
    counters["requests"] += 1
    try:
        domain = req.target_domain()
    except ValueError as e:
        counters["not_found"] += 1
        return JSONResponse(status_code=400, content=_error_body(e, 400))

    started = time.time()
    counters["in_flight"] += 1
    try:
        async with _semaphore:
            result = await asyncio.to_thread(scrape, domain, req.stars, req.max_reviews, req.months)
    except ValueError as e:  # clean_domain rejected it
        counters["not_found"] += 1
        return JSONResponse(status_code=400, content=_error_body(e, 400))
    except NoTrustpilotPage:
        # Apify's dataset was empty for a domain with no Trustpilot page, and the caller read that as
        # `no_results`. Answering a 404 error item instead made the same brand `request_failed`,
        # and because the message says "not found" it spent one of the brand's three retries on a
        # page that no retry will make appear. Measured 19 Sep 2026: 5 of the 26 baseline brands.
        # So: an empty array, with a header so the difference from "a page with no reviews in the
        # window" is still visible to anyone reading the response.
        counters["not_found"] += 1
        seconds = time.time() - started
        log.info("no page %s stars=%s %.1fs", domain, req.stars, seconds)
        return JSONResponse(content=[], headers={"X-No-Trustpilot-Page": "true", "X-Scrape-Seconds": f"{seconds:.1f}"})
    except TrustpilotError as e:
        key = "blocked" if e.status == 503 else "failed"
        counters[key] += 1
        log.warning("%s %s stars=%s -> %s: %s", e.status, domain, req.stars, type(e).__name__, e)
        return JSONResponse(status_code=e.status, content=_error_body(e, e.status))
    except Exception as e:  # noqa: BLE001
        counters["failed"] += 1
        log.exception("unexpected failure scraping %s", domain)
        return JSONResponse(status_code=500, content=_error_body(e, 500))
    finally:
        counters["in_flight"] -= 1

    items = to_items(result, req.include_company_info)
    counters["ok" if items else "empty"] += 1
    # A truncated call is a 200, so without this counter it is invisible to anything but a
    # per-request header read. A rising number here means the budget is being hit.
    if result.truncated:
        counters["truncated"] += 1
    log.info(
        "ok %s stars=%s reviews=%d/%d pages=%d %.1fs",
        result.domain, req.stars, len(items), result.total_count, result.pages_fetched, time.time() - started,
    )
    headers = {
        "X-Scrape-Seconds": str(result.seconds),
        "X-Pages-Fetched": str(result.pages_fetched),
        "X-Total-Available": str(result.total_count),
        "X-Trust-Score": str(result.business_unit.trust_score or ""),
        # Distinguishes "that is all the reviews there were" from "we hit the time budget".
        # Both are 200 with real reviews, so the body alone cannot tell you which happened.
        "X-Truncated": "true" if result.truncated else "false",
    }
    return JSONResponse(content=items, headers=headers)


@app.get("/health")
async def health():
    return {
        "status": "ok",
        "version": __version__,
        "auth": bool(settings.api_token),
        "proxy": bool(settings.proxy),
        "max_concurrency": settings.max_concurrency,
        **counters,
    }
