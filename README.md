# trustpilot-reviews

Self-hosted Trustpilot review scraper. A drop-in HTTP replacement for the Apify actor
(`automation-lab~trustpilot`) that the **Stage 4 – Brand Research Bundle** n8n workflow calls three
times per brand (1–2★, 3★, 4–5★).

## How it works

Trustpilot is a Next.js site: every review page embeds a `<script id="__NEXT_DATA__">` JSON blob with
the reviews, pagination and company info (TrustScore, review count). The scraper fetches the page with
[Scrapling](https://github.com/D4Vinci/Scrapling)'s stealth browser — Trustpilot's CloudFront bot
challenge blocks every plain HTTP client, including TLS-impersonating ones — parses that blob, and
returns items using the same field names Stage 4's `Sort Trustpilot Reviews` node already reads.

## Setup

```bash
uv sync
uv run scrapling install     # downloads the stealth browser (once)
cp .env.example .env         # set API_TOKEN before exposing the service
```

## Run

```bash
uv run trustpilot-reviews serve                       # HTTP service on :8000
uv run trustpilot-reviews scrape gymshark.com --stars 1,2 --max 20 --pretty   # one-off from the CLI
uv run pytest                                          # 33 tests, no network
```

## API

`POST /trustpilot` — `Authorization: Bearer <API_TOKEN>`

Accepts our own field names **or** the Apify body Stage 4 sends today, unchanged:

```json
{ "companyUrls": ["gymshark.com"], "stars": ["1","2"], "maxReviewsPerCompany": 30,
  "sort": "recency", "date": "last12months", "includeCompanyInfo": true }
```

```json
{ "domain": "gymshark.com", "stars": [1,2], "max": 30, "months": 12 }
```

Returns a JSON array of review items, newest first:

| Field | Notes |
|---|---|
| `reviewId`, `rating`, `title`, `text`, `language` | |
| `publishedDate`, `experienceDate`, `updatedDate` | ISO 8601 |
| `reviewerName`, `country`, `reviewerReviewCount`, `verified`, `verificationSource`, `likes` | |
| `replyMessage`, `replyDate` | company reply, if any |
| `companyUrl`, `companyDomain`, `companyName`, `companyWebsite` | used by Stage 4's wrong-company guard |
| `companyTrustScore`, `companyStars`, `companyTotalReviews`, `companyReviewsLast12Months` | used by Stage 4's sentiment label |

Response headers `X-Scrape-Seconds`, `X-Pages-Fetched`, `X-Total-Available`, `X-Trust-Score` help when
debugging from n8n.

### Error contract (matched to Stage 4's retry logic)

| Status | Meaning | Stage 4 treats it as |
|---|---|---|
| `404` | brand has no Trustpilot page — message contains `404 not found` | brand's problem: burns one of its 3 retries, then settles |
| `400` | invalid domain | brand's problem |
| `503` | blocked by the bot challenge, or the browser failed — message deliberately avoids `404` / `not found` | vendor failure: no retry burned |

Body is always `{"error": {"type", "status", "message", "description"}}`; the Apify nodes run with
`onError: continueRegularOutput`, so this surfaces as an error item the Sort node already handles.

`GET /health` — unauthenticated; counters (`ok`, `empty`, `not_found`, `blocked`, `failed`, `in_flight`).

## Configuration

See `.env.example`. The ones that matter: `API_TOKEN` (required in production), `SCRAPER_PROXY`
(add when the server's IP starts getting challenged), `MAX_CONCURRENCY` (3 = one brand's three bands
at once).

## Numbers from local testing (14 Sep 2026)

- One page (20 reviews) ≈ 13–20 s; two pages (30 reviews) ≈ 36 s. Stage 4 allows 250 s per band.
- Three bands in parallel for one brand: ≈ 37 s wall-clock.
- 10/10 fetches passed the challenge from a residential IP with no proxy and no cooldown.

## Swapping it into Stage 4

1. Deploy the service (Dockerfile provided, untested until the hosting step) and set `API_TOKEN`.
2. In n8n create an **Header Auth** credential: name `Authorization`, value `Bearer <API_TOKEN>`.
3. On the three `Apify: Trustpilot *` HTTP Request nodes: change the URL to
   `https://<host>/trustpilot`, switch auth to the new credential, drop the `maxTotalChargeUsd` /
   `timeout` query params. The JSON body can stay as-is. Keep the node names — the Sort node references
   them by name.
4. Rollback = `restore_workflow_version` to the previous Stage 4 version.
