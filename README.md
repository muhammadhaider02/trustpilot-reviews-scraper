<div align="center">

# Trustpilot Reviews

**FETCH. PARSE. SERVE.**

[![CI](https://github.com/haider-ecombench/trustpilot-reviews-scraper/actions/workflows/ci.yml/badge.svg)](https://github.com/haider-ecombench/trustpilot-reviews-scraper/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/Python-3.11+-3776AB?logo=python&logoColor=white)](https://python.org)
[![uv](https://img.shields.io/badge/uv-Package_Manager-DE5FE9?logo=uv&logoColor=white)](https://docs.astral.sh/uv/)
[![FastAPI](https://img.shields.io/badge/API-FastAPI-009688?logo=fastapi&logoColor=white)](https://fastapi.tiangolo.com)
[![Scrapling](https://img.shields.io/badge/Browser-Scrapling-FF6F00)](https://github.com/D4Vinci/Scrapling)

Self-hosted Trustpilot review scraper with an HTTP API.

[Architecture](docs/architecture.md) · [API](docs/api.md) · [Deployment](docs/deployment.md)

</div>

---

## Platform

This repo is a standalone review-collection service behind the SmartLead brand-research pipeline, running over its own HTTP API. It is the sibling of `reddit-reviews`, which serves Reddit the same way, and both stand in for the Apify actors the pipeline used to call.

## Quickstart

Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/). Runs as a single FastAPI process.

```bash
git clone https://github.com/haider-ecombench/trustpilot-reviews-scraper.git
cd trustpilot-reviews-scraper

uv sync                               # dependencies into a uv-managed venv
uv run scrapling install              # stealth browser for page scraping (once)

cp .env.example .env                  # then set API_TOKEN (see Configuration)

uv run trustpilot-reviews serve       # HTTP service on :8000
```

Verify with `curl localhost:8000/health` (expects `"status":"ok"`); interactive API docs are at `/docs`.

The service is driven over its HTTP API: one brand domain and a star band in, that brand's newest reviews out, in the Apify actor's request and response shape. See [api.md](docs/api.md) for the endpoint, auth (`Authorization: Bearer`) and the error contract.

## Configuration

All configuration is environment variables in `.env`. **`.env.example` is the canonical list.** Copy it and fill it in; the full reference with defaults lives in [architecture.md](docs/architecture.md#configuration-environment-variables).

The only required value is the API bearer token. The rest tune the browser, the per-call budget and an optional proxy.

## Development

```bash
uv run pytest            # 40 tests against saved __NEXT_DATA__ fixtures, no network
```

## Deployment

Production runs as a single Docker container on the Hostinger VPS that hosts n8n, attached to n8n's Docker network, with nothing published to the host. Deploys are a `git pull` and `docker compose up -d --build`; CI runs on push to `main`. The operational runbook (topology, env, checks and gotchas) is in [deployment.md](docs/deployment.md).
