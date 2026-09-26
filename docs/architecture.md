# Architecture

## What it replaces

The research workflow called the Apify actor `automation-lab~trustpilot` three times per brand, in parallel, one call per star band. The same three HTTP Request nodes now call this service with the same body:

| Node | `stars` | Node timeout |
|---|---|---|
| `Apify: Trustpilot 1 Star` | `["1", "2"]` | 250 s |
| `Apify: Trustpilot Mid` | `["3"]` | 250 s |
| `Apify: Trustpilot Positive` | `["4", "5"]` | 250 s |

The three responses go through `Merge Trustpilot Bands` (append, three inputs) into `Sort Trustpilot Reviews`. Banding at the source is what guarantees a mix of sentiment: one recency-sorted call without bands gave 51 negative, 0 mid and 5 positive reviews. This service therefore answers one band per call and never tries to balance anything itself.

What `Sort Trustpilot Reviews` does with the rows decides what this service has to return:

- An **error item** is any object carrying `error` or `message` with no `rating` and no `text`. One error item from any band marks the whole Trustpilot step `request_failed` and keeps nothing, even when the other two bands succeeded. That is the workflow's rule, not this service's, but it means a single `503` costs the brand all three bands.
- **Identity** is read from the company URL, not the company name: the slug of `companyUrl` (`/review/<slug>`), plus `companyDomain` and `companyWebsite`, must contain the brand's domain or its stem. Comparing a display name such as "BRAND NAME" to a domain stem such as "brandname" could never match, which is why the guard uses URL fields. A row with none of those fields passes the guard unchecked.
- **Stats** come from the first row that carries `companyTrustScore` or `companyTotalReviews`. The TrustScore sets the sentiment label; without it the label is inferred from the sample, which the workflow treats as a weaker basis.
- Text is `text`, rating is `rating`, dedup is on `reviewId`; reviews under 8 words or older than 1,095 days by `publishedDate` are dropped; up to 25 per band are kept, newest first. Outcomes: `request_failed`, `no_results`, `all_irrelevant`, `thin` (fewer than 6 kept), `ok`.

Then `Parse Report` classifies a `request_failed` outcome. It is a **vendor-side** failure unless the error text matches the brand pattern `\b404\b|not found|no such (company|business|page)|invalid (url|domain)`, in which case it is a **brand-side** failure. Only a brand-side failure counts against the brand; a vendor-side one is retried on a later run.

Three consequences for this code:

1. `companyUrl` is built from Trustpilot's own `identifyingName`, so it is the value the guard expects, and `includeCompanyInfo` should stay `true`. Turning it off removes every `company*` field, which both blinds the guard and drops the TrustScore.
2. A failure is one object with `error.description` and `error.message`, never an empty array. The workflow reads the description first. A domain with **no Trustpilot page** is not a failure: it is `200 []` (with `X-No-Trustpilot-Page: true`), because that is what Apify's empty dataset looked like and the workflow reads it as `no_results`. Answering a `404` there made the brand a brand-side `request_failed` over a page no retry can produce; 5 of a 26-brand baseline are in that state.
3. Message wording is part of the contract. A bad domain says `invalid domain`; blocked and crashed fetches say neither `404`, `not found`, `no such page` nor `invalid url`. `tests/test_scraper.py` pins this.

## How a page is read

Trustpilot is a Next.js site. Every review page embeds a `<script id="__NEXT_DATA__">` block holding the same structured data the page renders: `props.pageProps.reviews`, `businessUnit` (display name, identifying name, TrustScore, review counts, website, claimed and closed flags) and `filters.pagination`. The scraper reads that block with a regex and `json.loads`, never a CSS selector. The fixture under `tests/fixtures/` is a real dump of the gymshark.com negative-band page.

The URL is Trustpilot's own filter form:

```
https://www.trustpilot.com/review/<domain>?stars=1&stars=2&sort=recency&languages=en&date=last12months&page=2
```

Trustpilot offers four fixed windows (`last30days`, `last3months`, `last6months`, `last12months`); a requested month count maps to the smallest window that covers it, and anything over 12 means all time. Because the sort is by recency, the scraper also stops at the first review older than the requested months, so a `date` window that is wider than the request never over-fetches. The research workflow sends no month count: the API ignores Apify's `date` preset ([api.md](api.md#where-the-service-differs-from-the-actor) has the counts that showed the actor's output is usually the all-time set) and the workflow applies its own 1,095-day cut-off, so the `date=` parameter above is absent on its calls and a band is the 30 most recent reviews in that band, whatever their age.

A page is 20 reviews. `max` is accepted up to 100 but `MAX_PAGES` (3) caps a call at 60. The workflow asks for 30 per band, so a brand is at most six pages. Reviews are de-duplicated on id across pages, and a row whose rating is outside the requested band is dropped even though the server-side filter has always held.

## Why a browser

Trustpilot sits behind a CloudFront JavaScript challenge that rejects plain HTTP clients, including TLS-impersonating ones. Pages are fetched with Scrapling's `StealthyFetcher`, a headless Chromium. Every fetch launches a browser and tears it down again; there is no shared session.

Two arguments are load-bearing and recorded in `fetch_html`:

| Argument | Value | Why |
|---|---|---|
| `wait_selector` | `script#__NEXT_DATA__`, state `attached` | the challenge clears only if the browser is given time to run the page's JS: returning straight after `load` is `HTTP 403` in ~1.2 s on every page (measured locally and from the production host). Waiting for the blob that is parsed gives that time and returns the moment it exists: 4.7 / 5.0 / 5.0 s on three review pages, 3.6 s on a 404 page, blob present on all four. |
| `network_idle` | `False` | this was the original wait and it was the wrong one. Trustpilot keeps background requests open, so Scrapling's networkidle wait timed out silently at `FETCH_TIMEOUT_MS` on 49 of 78 fetches in one test run: 31.4 s per call with the data in hand after 5 s, which made a three-band brand cost ~71 s. Same four pages under it: 31.4 / 31.5 / 31.6 s and 31.3 s. |
| `retries` | `1` | Scrapling's default is 3, which multiplies with `fetch_page`'s 2 attempts and the 3-page loop: 18 navigations for one request, about 560 s worst case. The one retry that can tell a challenge from a dead browser lives in `fetch_page`. |

`disable_resources` blocks images, fonts, CSS and media, which is the largest memory lever because only the HTML document is read. `solve_cloudflare` is off: the challenge is CloudFront, not Cloudflare. `humanize`, `os_randomize` and `geoip` were removed: they are Camoufox-era arguments that Scrapling 0.4.15 silently ignores, so they read as active stealth while doing nothing.

No proxy is configured in production (`/health` reports `proxy: false`). `SCRAPER_PROXY` is a URL handed straight to Scrapling for the day the server's address starts being challenged; unlike the Reddit sibling there is no port pool here, because nothing has needed one.

## Retries and the error ladder

`fetch_page` makes two attempts per page with `PAGE_DELAY_S` between them, and retries on three things: the browser died (`ScrapeFailed`), a non-200 status (`ScrapeBlocked`, carrying the page title), or a 200 without `__NEXT_DATA__` (`ScrapeBlocked`, the challenge interstitial). It never retries a `404`; that is `NoTrustpilotPage` at once.

| Status | Type | Cause |
|---|---|---|
| `400` | `ValueError` | no domain in the body, or a domain with no dot after stripping scheme, `www.` and path |
| `200 []` | `NoTrustpilotPage` | Trustpilot answered `404` for `/review/<domain>`; the API turns it into an empty array with `X-No-Trustpilot-Page: true` |
| `503` | `ScrapeBlocked` | challenged or served a non-review page on both attempts |
| `503` | `ScrapeFailed` | Chromium or the network failed on both attempts |

## The time budget

The numbers only make sense together, and `.env.example` carries the arithmetic:

| | |
|---|---|
| one fetch | `FETCH_TIMEOUT_MS` = 30 s (observed ~32 s) |
| one page | 2 attempts × 30 s + `PAGE_DELAY_S` = 62 s |
| one call | 3 pages × 62 s + 2 × 2 s = 190 s |
| workflow node timeout | 250 s |

`FETCH_TIMEOUT_MS` caps dead time, not headroom. A fetch returns as soon as `__NEXT_DATA__` is attached, ~5 s, so this timeout is only reached by a page that never yields it (a challenge that does not clear), and that page then goes through `fetch_page`'s retry as `ScrapeBlocked`. Raising it makes that worst case worse and nothing else better. At 20,000 every call still returned a full 20 reviews; 30,000 leaves margin.

`SCRAPE_BUDGET_S` (200) is what protects the workflow. Nothing cancels a request once it starts: Starlette does not cancel a handler when the client disconnects, and the browser runs in a thread that cannot be cancelled, so a caller that gives up does not free the browser. Before each page after the first the scraper checks that a whole page's worst case still fits before the deadline; if not it returns what it has with `X-Truncated: true` and counts it. Page 1 is never skipped. `stop_grace_period` in `docker-compose.yml` is 210 s so a redeploy cannot kill a scrape mid-budget.

## Memory and concurrency

Usage is spiky, not cumulative: one Chromium per fetch at about 430 MiB, torn down after, plus about 250 MB for the Python process. Three concurrent fetches peaked at 994 MiB against the 3 GiB cap. PIDs peaked at 413 while three browsers were launching and 146 with five already running; a cap of 512 was not enough, and hitting it surfaces as an opaque `Target closed`, so the cap is 1,024.

`MAX_CONCURRENCY` (3) is an `asyncio.Semaphore` sized to the workflow's three band calls for one brand. A second brand overlapping the first queues. Measured through a test workflow on the same n8n instance, six calls fired together (two brands, three bands each): three finished in 12 to 33 s, the other three waited for a permit and finished in 43 to 65 s, and all six returned 20 reviews.

## Layout

| File | Role |
|---|---|
| `src/trustpilot_reviews/scraper.py` | URL building, `__NEXT_DATA__` parsing, the browser fetch, retry and the page loop with the budget |
| `src/trustpilot_reviews/mapping.py` | turns parsed reviews into the Apify row shape the workflow reads |
| `src/trustpilot_reviews/api.py` | FastAPI surface: request aliases, bearer check, error bodies, headers, counters |
| `src/trustpilot_reviews/config.py` | environment variables, read once |
| `src/trustpilot_reviews/__init__.py` | the `trustpilot-reviews` CLI (`serve`, `scrape`) |
| `tests/` | 41 tests against the saved fixture; no network |

## Configuration (environment variables)

| Variable | Default | Meaning |
|---|---|---|
| `API_TOKEN` | *(empty)* | bearer token callers must send; empty disables auth, for local testing only |
| `SCRAPER_PROXY` | *(empty)* | proxy URL (`http://user:pass@host:port`) passed to the browser |
| `MAX_CONCURRENCY` | `3` | browser fetches in flight; raise `mem_limit` with it |
| `FETCH_TIMEOUT_MS` | `30000` | per-page browser timeout; caps dead time, see above |
| `BLOCK_RESOURCES` | `true` | block images, fonts, CSS and media in the browser |
| `MAX_PAGES` | `3` | pages per call, 20 reviews each |
| `SCRAPE_BUDGET_S` | `200` | wall-clock ceiling per call; must stay under the workflow's 250 s node timeout |
| `PAGE_DELAY_S` | `2` | pause between pages and before a retry |
| `SOLVE_CLOUDFLARE` | `false` | Scrapling's Cloudflare solver; not needed for CloudFront |
| `HOST` | `0.0.0.0` | bind address |
| `PORT` | `8000` | bind port |
