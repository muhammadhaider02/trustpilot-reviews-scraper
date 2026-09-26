# API

One endpoint that answers the body the research workflow sends to the Apify actor `automation-lab~trustpilot` three times per brand (one call per star band), in the actor's row shape. Interactive docs are at `/docs`.

## The Apify contract

| | Apify | This service |
|---|---|---|
| Endpoint | `POST https://api.apify.com/v2/acts/automation-lab~trustpilot/run-sync-get-dataset-items` | `POST http://trustpilot-reviews:8000/trustpilot` |
| Auth | n8n `apifyApi` credential | n8n Header Auth credential sending `Authorization: Bearer <API_TOKEN>` |
| Query string | `maxTotalChargeUsd` and `timeout` | none |
| Success | JSON array, one object per review | same |
| Failure | one object carrying `error` or `message`, no `rating` | same |
| No Trustpilot page for the domain | empty dataset, `[]` | `[]`, plus `X-No-Trustpilot-Page: true` |
| Node timeout | 250 s per band | unchanged; `SCRAPE_BUDGET_S` sits under it |

The node options `alwaysOutputData` and `onError: continueRegularOutput` are what let an error body reach `Sort Trustpilot Reviews` instead of stopping the run. Keep both. [deployment.md](deployment.md#pointing-an-n8n-workflow-at-it) has the node settings.

### The band call

Sent verbatim by each of the three band nodes; only `stars` differs (`["1", "2"]`, `["3"]`, `["4", "5"]`).

```json
{
  "companyUrls": [ "<domain_clean>" ],
  "stars": ["1", "2"],
  "maxReviewsPerCompany": 30,
  "sort": "recency",
  "date": "last12months",
  "includeCompanyInfo": true
}
```

Honoured: `companyUrls` (first entry only), `stars`, `maxReviewsPerCompany`, `includeCompanyInfo`. `sort` is accepted and ignored: the service always sorts by recency. `date` is accepted and ignored too, because that is what the actor's output does with it (next section).

### Where the service differs from the actor

- One company per call. Only the first entry of `companyUrls` is read; the workflow sends exactly one.
- `date` is ignored. Applying `last12months` as Trustpilot's own window and as a `publishedDate` cut-off lost four brands of a 26-brand baseline measured against the actor's stored output for the same body: fieldcrestproducts.com has 14 reviews and 0 in the last 12 months, and Apify returned all 14 (8 kept after the workflow's 3-year filter); apexlift.com 3 and 0 (Apify kept 2); quarrytech.com 1 review from 2021, which Apify returned; talon9.com 7,287 and 2 in the window (Apify kept 11, exactly the 11 under 3 years, and no 12-month window at any date holds more than 5 of them). The actor's output is not windowed consistently, though: fieldcrest.com (Apify 21) and ridgelinesports.com (Apify 29) equal the 12-month window exactly and their all-time sets are 32 and 42. No single rule reproduces both groups. Ignoring the window is the choice that is never below the baseline on any brand, and the workflow applies its own 1,095-day cut-off. An integer `months` in the native shape still applies a window.
- Reviews are de-duplicated on id across pages before they are returned.
- A partial result is a `200`. If the time budget stops the scrape before `max`, the reviews already collected are returned and `X-Truncated: true` is set; the body looks identical to a complete run.

## Request fields

`POST /trustpilot` takes a JSON body. Unknown fields are ignored.

| Field | Aliases | Default | Notes |
|---|---|---|---|
| `domain` | `domain_clean` | | brand domain; scheme, `www.` and path are stripped; must contain a dot |
| `companyUrls` | `company_urls` | | list; used when `domain` is absent, first entry only |
| `stars` | | `[1,2,3,4,5]` | list or comma-separated string; numbers or strings; entries outside 1–5 dropped; nothing valid means all five |
| `max` | `maxReviewsPerCompany`, `max_reviews` | `20` | 1–100, then capped by `MAX_PAGES` × 20 |
| `months` | | none (all time) | integer; only reviews published in the last N months, as Trustpilot's smallest covering window plus a `publishedDate` cut-off |
| `date` | | | accepted and ignored: Apify's presets did not consistently narrow its output, so honouring them here broke parity (see above) |
| `includeCompanyInfo` | `include_company_info` | `true` | `false` drops every `company*` field; the workflow's wrong-company guard and TrustScore need them, so leave it on |

## Response rows

One object per review, newest first, company fields repeated on every row.

| Field | Value | Read downstream by |
|---|---|---|
| `reviewId` | Trustpilot review id | dedup key in `Sort Trustpilot Reviews` |
| `rating` | 1–5 | band assignment; a row without one is skipped |
| `title` | | 200 chars kept |
| `text` | review body | 8-word minimum, 900 chars kept; a row without one is skipped |
| `publishedDate` | ISO 8601 | age; older than 1,095 days is dropped |
| `country` | reviewer country code | 4 chars kept |
| `companyUrl` | `https://www.trustpilot.com/review/<identifyingName>` | wrong-company guard: the slug must contain the brand's domain or stem |
| `companyDomain`, `companyWebsite` | `<identifyingName>`, business website | wrong-company guard |
| `companyName` | display name | stats, and the guard's fallback when no URL fields are present |
| `companyTrustScore`, `companyStars` | TrustScore, star rating | sentiment label |
| `companyTotalReviews` | | stats line in the report |
| `experienceDate` | ISO 8601 | fallback for age when `publishedDate` is empty |
| `reviewUrl`, `language`, `updatedDate`, `reviewerName`, `reviewerReviewCount`, `verified`, `verificationSource`, `likes`, `replyMessage`, `replyDate` | | not read |
| `companyReviewsLast12Months`, `companyCountry`, `companyIsClosed`, `companyIsClaimed` | | not read |

## Response headers

| Header | Meaning |
|---|---|
| `X-Scrape-Seconds` | wall clock for the call |
| `X-Pages-Fetched` | pages read, retries not counted |
| `X-Total-Available` | reviews Trustpilot reports for the band and window, before `max` |
| `X-Trust-Score` | the company's TrustScore, empty if the page had none |
| `X-Truncated` | `true` when `SCRAPE_BUDGET_S` stopped the call with pages left |
| `X-No-Trustpilot-Page` | `true` when the `[]` is because Trustpilot has no page for the domain, as opposed to a page with no reviews in the window |

## Errors

```json
{ "error": { "type": "ValueError", "status": 400, "message": "invalid domain: request must include `domain` (or `companyUrls`)", "description": "..." } }
```

| Status | Type | When |
|---|---|---|
| `400` | `ValueError` | no `domain` or `companyUrls`, or a domain with no dot |
| `401` | | missing or wrong bearer token |
| `503` | `ScrapeBlocked` | challenged, or served a page without `__NEXT_DATA__`, on both attempts |
| `503` | `ScrapeFailed` | the browser or network failed on both attempts |
| `500` | | anything unexpected |

The wording is part of the contract. The workflow reads any error item as `request_failed` and then classifies it: a message matching `404`, `not found`, `no such page` or `invalid url|domain` is a **brand-side** failure (the brand's own input is bad); anything else is a **vendor-side** failure (the scraper could not fetch, and a later run may succeed). So the `400` message carries those words and the `503` messages never do. One error item from any band discards all three bands' reviews for that run; that is the workflow's rule.

A domain with no Trustpilot page is **not** an error. Apify returned an empty dataset for such domains and the workflow reads that as `no_results`, so this service returns `200 []` with `X-No-Trustpilot-Page: true`. An earlier `404` whose message said `not found` made the workflow classify the brand as a brand-side `request_failed` for a page no retry can produce. In the 26-brand baseline 5 brands are in this state.

## `GET /health`

Unauthenticated, for uptime checks and the Docker `HEALTHCHECK`.

```json
{
  "status": "ok", "version": "0.1.0", "auth": true, "proxy": false, "max_concurrency": 3,
  "requests": 12, "ok": 12, "empty": 0, "truncated": 0, "not_found": 0,
  "blocked": 0, "failed": 0, "in_flight": 0
}
```

Counters reset on restart. `not_found` counts missing pages (`200 []` with `X-No-Trustpilot-Page`) and `400`s together; `blocked` is `503`; `failed` is `500`. `truncated` rising means the budget is biting, which a `200` would otherwise hide.

## Command line

Same code, no server:

```bash
uv run trustpilot-reviews scrape gymshark.com --stars 1,2 --max 20 --months 12 --pretty
```
