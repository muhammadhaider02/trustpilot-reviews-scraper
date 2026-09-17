"""HTTP surface n8n calls in place of Apify's run-sync-get-dataset-items endpoint.

POST /trustpilot  -> JSON array of review items (same names Stage 4's Sort node reads from Apify)
GET  /health      -> counters, useful for Pipeline Health Check

A 200 may be a partial result: if the scrape hits SCRAPE_BUDGET_S it returns the reviews it
already collected and sets `X-Truncated: true`. The body looks identical to a complete run.

Error contract, matched to Stage 4's retry logic:
  404 {"error": {...}}  brand has no Trustpilot page -> Stage 4 burns one of the brand's retries
  400 {"error": {...}}  invalid domain               -> same, brand-side
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
from .scraper import TrustpilotError, scrape

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
    """Accepts both our own field names and the Apify body Stage 4 sends today, so the n8n change
    can be as small as swapping the URL."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    domain: str | None = Field(default=None, validation_alias=AliasChoices("domain", "domain_clean"))
    company_urls: list[str] | None = Field(default=None, validation_alias=AliasChoices("companyUrls", "company_urls"))
    stars: list[int] = Field(default_factory=lambda: [1, 2, 3, 4, 5])
    max_reviews: int = Field(default=20, ge=1, le=100, validation_alias=AliasChoices("max", "maxReviewsPerCompany", "max_reviews"))
    months: int | None = Field(default=12, validation_alias=AliasChoices("months", "date"))
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

    @field_validator("months", mode="before")
    @classmethod
    def _coerce_months(cls, v):
        # Apify took a `date` string like "last12months"; map that back to a month count.
        if isinstance(v, str):
            table = {"last30days": 1, "last3months": 3, "last6months": 6, "last12months": 12, "all": None, "": None}
            if v in table:
                return table[v]
            try:
                return int(v)
            except ValueError:
                return 12
        return v

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
    except TrustpilotError as e:
        key = "not_found" if e.status == 404 else "blocked" if e.status == 503 else "failed"
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
