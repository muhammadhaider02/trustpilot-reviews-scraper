# Deployment

## Where it runs

| | |
|---|---|
| Host | a Docker host that also runs n8n |
| Checkout | `~/trustpilot-reviews-scraper`, a clone of `main` |
| Container | `trustpilot-reviews`, built from the `Dockerfile` by `docker-compose.yml` |
| Network | `n8n_default`, the network n8n's own Compose project created; declared external so this file never owns it |
| Address from n8n | `http://trustpilot-reviews:8000/trustpilot` |
| Beside it | `reddit-reviews` on `8001` and `facebook-ad-library` on `8003`, deployed the same way |
| Proxy | none by default; `/health` reports `proxy: false` |

Nothing is published to the host. Docker publishes straight past UFW, so even `8000:8000` behind the firewall would be public; the service is reachable only from containers on the network, and `API_TOKEN` still applies so a compromised container cannot drive it freely. There is no domain, no certificate and no reverse proxy for this service. If the host already runs a reverse proxy on 80/443 (for n8n's own UI, say), do not add a second one: it would fail to bind.

## What the compose file sets, and why

| Setting | Value | Reason |
|---|---|---|
| `mem_limit` / `memswap_limit` | 3g / 3g | three concurrent Chromiums peaked at 994 MiB; swap off so a thrashing browser dies instead of crawling. The host is shared with n8n (~650 MB), so budget against what is left, not total RAM; on a 4 GB host use 2g and `MAX_CONCURRENCY=2` |
| `mem_reservation` | 1g | soft limit, enforced only when the host is short of memory |
| `pids_limit` | 1024 | 413 PIDs measured while three browsers launched; 512 was not enough, and the failure reads as `Target closed`, not as a limit |
| `init: true` | | reaps Chromium's orphaned process trees after a timed-out fetch |
| `shm_size` | 256m | insurance for Chromium paths that ignore `--disable-dev-shm-usage`; deliberately not a tmpfs `/tmp`, which would count against `mem_limit` |
| `stop_grace_period` | 210s | `SCRAPE_BUDGET_S` plus teardown, so a redeploy never kills an in-flight scrape |
| logging | json-file, 10m × 3 | Chromium is verbose enough to fill the disk otherwise |

`MAX_CONCURRENCY` is what keeps the container inside its budget; `mem_limit` is the blast-radius guard for the host. Raise them together.

## First deploy

```bash
git clone https://github.com/automation-ecombench/trustpilot-reviews-scraper.git ~/trustpilot-reviews-scraper
cd ~/trustpilot-reviews-scraper
cp .env.example .env          # set API_TOKEN
docker compose up -d --build  # first build is slow: it downloads Chromium
```

If the network name is wrong the container refuses to start with `network n8n_default declared as external, but could not be found`; `docker network ls` gives the real one.

## Update, rollback, logs

```bash
cd ~/trustpilot-reviews-scraper
git pull origin main && docker compose up -d --build        # deploy main
git checkout <sha> && docker compose up -d --build           # roll back to a known commit
docker compose logs -f --tail 100                            # follow the service log
```

`.env` is read at container start (`env_file`), so a changed value needs `docker compose up -d`, not a restart of the process inside. Keep a dated copy before editing it.

The container has no `curl`. Read health from inside it with:

```bash
docker exec trustpilot-reviews python -c "import urllib.request;print(urllib.request.urlopen('http://127.0.0.1:8000/health').read().decode())"
```

## Verifying a deploy

1. Run a real scrape from the server:
   ```bash
   docker compose exec scraper uv run --no-sync trustpilot-reviews scrape gymshark.com --stars 1,2 --max 5
   ```
   Expect five reviews and a `trust_score`. A `503` on a domain that worked yesterday means the server's address is being challenged; set `SCRAPER_PROXY` before anything else.
2. `/health` should show `auth: true` and `max_concurrency: 3`.
3. Each successful call logs one line: `ok <domain> stars=[1, 2] reviews=20/1126 pages=1 32.0s`. The second number is what Trustpilot reports as available for the band.

## Rotating credentials

`API_TOKEN`: change it in `.env`, recreate the container, then update the n8n Header Auth credential that sends `Authorization: Bearer <token>` to match. Retrieve tokens in a terminal, not in anything that keeps a transcript.

## Pointing an n8n workflow at it

The research workflow's three band nodes (`Apify: Trustpilot 1 Star`, `Apify: Trustpilot Mid`, `Apify: Trustpilot Positive`) are HTTP Request nodes. To move any Apify-shaped caller over:

- URL: `http://trustpilot-reviews:8000/trustpilot`, which resolves only from containers on the n8n network.
- Authentication: a Header Auth credential sending `Authorization: Bearer <API_TOKEN>`.
- Drop Apify's `maxTotalChargeUsd` and `timeout` query parameters. The body is unchanged; only `stars` differs between the three band calls.
- Keep the node timeout at 250 s, above `SCRAPE_BUDGET_S`.
- Set On Error to continue (`continueRegularOutput`, with `alwaysOutputData`) so a `503` arrives as an item the next node can classify, instead of stopping the run.

Keep the node names and their three inputs into `Merge Trustpilot Bands`. Six calls fired at once (two brands, three bands each) all returned 20 reviews: three ran together and three queued behind the semaphore, worst case 65 s.

## What to watch

| Signal | Meaning |
|---|---|
| `/health` `blocked` rising | Trustpilot is challenging the server's address; read the page title in the log, then add a proxy |
| `/health` `failed` rising | Chromium is crashing; check `docker stats` and the PID count before blaming the site |
| `/health` `truncated` rising | `SCRAPE_BUDGET_S` is biting; too many brands overlapping on the semaphore |
| `/health` `not_found` rising across many brands | either the brand list is bad or `clean_domain` is rejecting a new input shape; a real missing page is one brand at a time |
| `docker stats` memory near 3g | raise `mem_limit` before raising `MAX_CONCURRENCY`, never the other way round |

## CI

`.github/workflows/ci.yml` runs on pushes to `main`, pull requests and manual dispatch:

- **unit-tests**: `uv run pytest -q` on Python 3.11.
- **docker-image**: builds the image, starts it with a token minted for that run only, and checks `/health`, that a missing token is `401`, a live scrape of gymshark.com (three-star band, max 5, twelve months) that must return rated reviews with a TrustScore, and that an unknown domain is a `200` whose body is `[]` with `X-No-Trustpilot-Page: true`. A `503` fails the build: unlike the Reddit sibling there is no allowance for being blocked.
