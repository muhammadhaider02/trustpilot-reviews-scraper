<div align="center">

# Trustpilot Reviews

**FETCH. PARSE. SERVE.**

[![CI](https://github.com/automation-ecombench/trustpilot-reviews-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/automation-ecombench/trustpilot-reviews-scraper/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Scrapling](https://img.shields.io/badge/Browser-Scrapling-FF6F00)](https://github.com/D4Vinci/Scrapling)

Self-hosted Trustpilot review scraper with an HTTP API.

[Architecture](docs/architecture.md) · [API](docs/api.md) · [Deployment](docs/deployment.md)

</div>

---

## What it is

A FastAPI service that loads a brand's Trustpilot review pages in a headless browser (Scrapling over Playwright's Chromium) and reads the reviews out of the page's embedded `__NEXT_DATA__` JSON. `POST /trustpilot` takes one brand domain and a star band and returns that brand's newest reviews in the request and response shape of the Apify actor `automation-lab~trustpilot`, so a caller can swap the URL and keep its body and parsing.

It is one of three services behind an n8n brand-research pipeline, beside `reddit-reviews` (port 8001) and `facebook-ad-library` (port 8003), and it replaces a paid Apify actor. This one listens on 8000.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Runs as a single FastAPI process.

```bash
git clone https://github.com/automation-ecombench/trustpilot-reviews-scraper.git
cd trustpilot-reviews-scraper

uv sync                               # dependencies into a uv-managed venv
uv run scrapling install              # stealth browser for page scraping (once)

cp .env.example .env                  # then set API_TOKEN (see Configuration)

uv run trustpilot-reviews serve       # HTTP service on :8000
```

Verify with `curl localhost:8000/health` (expects `"status":"ok"`); interactive API docs are at `/docs`. See [api.md](docs/api.md) for the endpoint, auth (`Authorization: Bearer`) and the error contract. The same code runs without a server:

```bash
uv run trustpilot-reviews scrape gymshark.com --stars 1,2 --max 20 --pretty
```

## Configuration

All configuration is environment variables in `.env`. **`.env.example` is the canonical list**; the reference with defaults is in [architecture.md](docs/architecture.md#configuration-environment-variables). The only required value is `API_TOKEN`. The rest tune the browser, the per-call time budget and an optional proxy.

## Development

```bash
uv run pytest            # 41 tests against a saved __NEXT_DATA__ fixture, no network
```

## Deployment

One Docker container on a host that also runs n8n, attached to n8n's Docker network, with nothing published to the host. Deploys are a `git pull` and `docker compose up -d --build`; CI runs on push to `main` and on pull requests. The runbook (topology, env, checks, what to watch) is in [deployment.md](docs/deployment.md).

## Disclaimer

This service reads public Trustpilot pages. You are responsible for making sure your use complies with Trustpilot's terms of service and with applicable law. It is not affiliated with, endorsed by or supported by Trustpilot or Apify.

## License

[MIT](LICENSE)
