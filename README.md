<div align="center">

# Trustpilot Reviews

**FETCH. PARSE. SERVE.**

[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Scrapling](https://img.shields.io/badge/Browser-Scrapling-FF6F00)](https://github.com/D4Vinci/Scrapling)

Self-hosted Trustpilot review scraper with an HTTP API.

</div>

---

## Platform

This repo is a standalone review-collection service. Given a brand domain it fetches that brand's Trustpilot page with a stealth browser, reads the structured `__NEXT_DATA__` JSON the page embeds (reviews, pagination, TrustScore, review count), and serves the result over a small authenticated HTTP API that automation workflows call like any other data service.

A real browser is required: Trustpilot sits behind a CloudFront bot challenge that rejects every plain HTTP client, including TLS-impersonating ones.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Runs as a single FastAPI process.

```bash
git clone https://github.com/haider-ecombench/trustpilot-reviews-scraper.git
cd trustpilot-reviews-scraper

uv sync                       # dependencies into a uv-managed venv
uv run scrapling install      # stealth browser (once)

cp .env.example .env          # then set API_TOKEN (see Configuration)

uv run trustpilot-reviews serve                    # HTTP service on :8000
```

Verify with `curl localhost:8000/health` (expects `"status":"ok"`); interactive API docs are at `/docs`.

The service is driven over its HTTP API: `POST /trustpilot` takes a brand domain with `Authorization: Bearer <API_TOKEN>` and returns that brand's reviews newest first, while `GET /health` is unauthenticated and returns request counters. See `/docs` for the request fields, the response shape and the error contract.

One-off scrapes from the command line, no server needed:

```bash
uv run trustpilot-reviews scrape gymshark.com --stars 1,2 --max 20 --pretty
```

## Configuration

All configuration is environment variables in `.env`. **`.env.example` is the canonical list.** Copy it and fill it in; every variable is documented there alongside the measurement its default is based on.

`API_TOKEN` is the bearer token callers must send, and is required in production. The rest tune the browser: concurrency, per-page timeout, page cap, resource blocking and an optional proxy.

`SCRAPE_BUDGET_S` bounds a single call's wall clock. Past it the service returns the reviews it has already collected and sets an `X-Truncated` response header, rather than running on past the caller's own timeout holding a browser nobody is waiting for.

## Development

```bash
uv run pytest        # 40 tests against saved __NEXT_DATA__ fixtures, no network
```

Layout: `scraper.py` (fetch, parse, paginate, retry), `mapping.py` (output shape), `api.py` (FastAPI surface), `config.py` (env). Fixtures in `tests/fixtures/` are real page dumps; refresh them if Trustpilot changes its page structure.

## Deployment

Production runs as a single Docker container on a Linux VPS, behind a TLS reverse proxy. `docker-compose.yml` is the deployment topology. It carries the memory limits, process reaping and log rotation that a bare `docker run` would not, and its comments record the measurements each limit is sized against.

```bash
git clone https://github.com/haider-ecombench/trustpilot-reviews-scraper.git
cd trustpilot-reviews-scraper
cp .env.example .env          # set API_TOKEN
docker compose up -d --build  # first build is slow: it downloads Chromium
```

Then point `Caddyfile` at your domain and `caddy reload`. The container publishes on `127.0.0.1:8000` only, so the reverse proxy is the sole public route in. **Note that `docker run -p 8000:8000` would bypass UFW entirely and expose the API publicly**.

CI runs on pushes to `main` and on pull requests: unit tests, plus a full image build that starts the container and exercises `/health`, token enforcement, a live scrape and the `404` path.
