<div align="center">

# Trustpilot Reviews

**FETCH. PARSE. SERVE.**

[![CI](https://github.com/haider-ecombench/trustpilot-reviews/actions/workflows/ci.yml/badge.svg)](https://github.com/haider-ecombench/trustpilot-reviews/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Scrapling](https://img.shields.io/badge/Browser-Scrapling-FF6F00)](https://github.com/D4Vinci/Scrapling)

Self-hosted Trustpilot review scraper with an HTTP API.

[Quickstart](#quickstart) · [API](#api) · [Configuration](#configuration) · [Development](#development) · [Deployment](#deployment)

</div>

---

## Platform

This repo is a standalone review-collection service. Given a brand domain it fetches that brand's Trustpilot page with a stealth browser, reads the structured `__NEXT_DATA__` JSON the page embeds (reviews, pagination, TrustScore, review count), and serves the result over a small authenticated HTTP API that automation workflows call like any other data service.

A real browser is required: Trustpilot sits behind a CloudFront bot challenge that rejects every plain HTTP client, including TLS-impersonating ones.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Runs as a single FastAPI process.

```bash
git clone https://github.com/haider-ecombench/trustpilot-reviews.git
cd trustpilot-reviews

uv sync                       # dependencies into a uv-managed venv
uv run scrapling install      # stealth browser (once)

cp .env.example .env          # then set API_TOKEN (see Configuration)

uv run trustpilot-reviews serve                    # HTTP service on :8000
```

Verify with `curl localhost:8000/health` (expects `"status":"ok"`); interactive API docs are at `/docs`.

One-off scrapes from the command line, no server needed:

```bash
uv run trustpilot-reviews scrape gymshark.com --stars 1,2 --max 20 --pretty
```

## API

`POST /trustpilot` with `Authorization: Bearer <API_TOKEN>`

```json
{ "domain": "gymshark.com", "stars": [1, 2], "max": 30, "months": 12 }
```

| Field | Default | Notes |
|---|---|---|
| `domain` | required | brand domain; URLs and `www.` are normalised |
| `stars` | `[1,2,3,4,5]` | star ratings to include; filtered server-side by Trustpilot |
| `max` | `20` | reviews to return, newest first (20 per page, capped by `MAX_PAGES`) |
| `months` | `12` | date window: 1, 3, 6, 12, or `null` for all time |
| `includeCompanyInfo` | `true` | attach TrustScore, review count and company URL to every item |

Returns a JSON array of review items, newest first. Each item carries the review (`reviewId`, `rating`, `title`, `text`, `language`, `publishedDate`, `experienceDate`, `reviewerName`, `country`, `verified`, `likes`, `replyMessage`, …) and, when requested, the company (`companyUrl`, `companyDomain`, `companyName`, `companyWebsite`, `companyTrustScore`, `companyTotalReviews`, `companyReviewsLast12Months`, …).

Response headers `X-Scrape-Seconds`, `X-Pages-Fetched`, `X-Total-Available` and `X-Trust-Score` help when debugging a caller.

| Status | Meaning |
|---|---|
| `200` | review items (possibly an empty array) |
| `400` | invalid domain |
| `401` | missing or wrong bearer token |
| `404` | the brand has no Trustpilot page |
| `503` | the fetch was blocked by the bot challenge, or the browser failed |

Error bodies are `{"error": {"type", "status", "message", "description"}}`. `404` and `400` are the caller's problem; `503` is ours.

`GET /health` is unauthenticated and returns request counters (`ok`, `empty`, `not_found`, `blocked`, `failed`, `in_flight`).

## Configuration

All configuration is environment variables in `.env`. **`.env.example` is the canonical list.**

| Variable | Default | Purpose |
|---|---|---|
| `API_TOKEN` | *(empty)* | bearer token callers must send; required in production |
| `SCRAPER_PROXY` | *(none)* | proxy URL for the browser; add when the server's IP starts being challenged |
| `MAX_CONCURRENCY` | `3` | browser fetches allowed at once |
| `FETCH_TIMEOUT_MS` | `60000` | per-page browser timeout |
| `MAX_PAGES` | `3` | hard cap on pages per request |
| `PAGE_DELAY_S` | `2` | pause between pages and before a retry |
| `PORT` | `8000` | listen port |

## Development

```bash
uv run pytest        # 33 tests against saved __NEXT_DATA__ fixtures, no network
```

Layout: `scraper.py` (fetch, parse, paginate, retry), `mapping.py` (output shape), `api.py` (FastAPI surface), `config.py` (env). Fixtures in `tests/fixtures/` are real page dumps; refresh them if Trustpilot changes its page structure.

Measured on a residential connection: one page (20 reviews) in 13–20 s, two pages in about 36 s; roughly 60 s per page from a datacenter IP.

## Deployment

Production runs as a single Docker container on a Linux VPS. The `Dockerfile` installs the stealth browser into `/opt/cache` and runs the service as a non-root user on port 8000; put a reverse proxy with TLS in front of it and set `API_TOKEN`.

```bash
docker build -t trustpilot-reviews .
docker run -d --restart unless-stopped -p 8000:8000 --env-file .env trustpilot-reviews
```

GitHub Actions CI runs on every push to `main`: the unit tests, then a full image build that starts the container and exercises `/health`, token enforcement, a live scrape and the `404` path.
