# Deployment

## Where it runs

| | |
|---|---|
| Host | the Hostinger VPS that runs the self-hosted n8n at `n8n.srv1980669.hstgr.cloud` |
| Checkout | `/opt/trustpilot-reviews-scraper`, a clone of `main` |
| Container | `trustpilot-reviews`, built from the `Dockerfile` by `docker-compose.yml` |
| Network | `n8n_default`, the network n8n's own Compose project created; declared external so this file never owns it |
| Address from n8n | `http://trustpilot-reviews:8000/trustpilot` |
| Beside it | `reddit-reviews` on `8001`, deployed the same way |
| Proxy | none; `/health` reports `proxy: false` |

Nothing is published to the host. Docker publishes straight past UFW, so even `8000:8000` behind the firewall would be public; the service is reachable only from containers on the network, and `API_TOKEN` still applies so a compromised container cannot drive it freely. The VPS already runs Traefik on 80/443 for n8n. There is no domain, no certificate and no reverse proxy for this service, and adding one would fail to bind.

## What the compose file sets, and why

| Setting | Value | Reason |
|---|---|---|
| `mem_limit` / `memswap_limit` | 3g / 3g | three concurrent Chromiums peaked at 994 MiB; swap off so a thrashing browser dies instead of crawling. The box is shared with n8n (~650 MB) and Traefik, so budget against what is left, not total RAM; on a 4 GB VPS use 2g and `MAX_CONCURRENCY=2` |
| `mem_reservation` | 1g | soft limit, enforced only when the host is short of memory |
| `pids_limit` | 1024 | 413 PIDs measured while three browsers launched; 512 was not enough, and the failure reads as `Target closed`, not as a limit |
| `init: true` | | reaps Chromium's orphaned process trees after a timed-out fetch |
| `shm_size` | 256m | insurance for Chromium paths that ignore `--disable-dev-shm-usage`; deliberately not a tmpfs `/tmp`, which would count against `mem_limit` |
| `stop_grace_period` | 210s | `SCRAPE_BUDGET_S` plus teardown, so a redeploy never kills an in-flight scrape |
| logging | json-file, 10m × 3 | Chromium is verbose enough to fill the disk otherwise |

`MAX_CONCURRENCY` is what keeps the container inside its budget; `mem_limit` is the blast-radius guard for the host. Raise them together.

## First deploy

```bash
git clone https://github.com/haider-ecombench/trustpilot-reviews-scraper.git /opt/trustpilot-reviews-scraper
cd /opt/trustpilot-reviews-scraper
cp .env.example .env          # set API_TOKEN
docker compose up -d --build  # first build is slow: it downloads Chromium
```

If the network name is wrong the container refuses to start with `network n8n_default declared as external, but could not be found`; `docker network ls` gives the real one.

## Update, rollback, logs

```bash
cd /opt/trustpilot-reviews-scraper
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
   Expect five reviews and a `trust_score`. A `503` on a domain that worked yesterday means the VPS address is being challenged; set `SCRAPER_PROXY` before anything else.
2. `/health` should show `auth: true` and `max_concurrency: 3`.
3. Each successful call logs one line: `ok <domain> stars=[1, 2] reviews=20/1126 pages=1 32.0s`. The second number is what Trustpilot reports as available for the band.

## Rotating credentials

`API_TOKEN`: change it in `.env`, recreate the container, then update the n8n Header Auth credential `trustpilot-scraper` to match. It is a Header Auth credential sending `Authorization: Bearer <token>`. Retrieve tokens in a terminal, not in anything that keeps a transcript.

## Testing against the pipeline

The production workflow is not edited. Changes are exercised in the n8n workflow **`scraper-testing`** (`0q7jtSF7FG0cbyBe`) on the same instance, which holds three Trustpilot nodes, currently disabled while the Reddit chain in the same workflow is under test. Enable them to run them.

| Node | What it does |
|---|---|
| `Scraper Health` | `GET http://trustpilot-reviews:8000/health` over the network, which is the only place that address resolves |
| `Scrape Brand` | one band call with the Apify body verbatim (`nobltravel.com`, stars 1–2, max 20, months 12), 240 s timeout, the `trustpilot-scraper` credential |
| `Three Bands` → `Scrape Band` | two brands × three bands as one batch of six with no interval, which is what Stage 4 produces when one brand starts before the previous one finishes; this is the `MAX_CONCURRENCY` and memory test |

The last six-call run on 17 Sep 2026 returned 20 reviews on every call; three ran at once and three queued behind the semaphore, worst case 65 s.

## What to watch

| Signal | Meaning |
|---|---|
| `/health` `blocked` rising | Trustpilot is challenging the VPS address; read the page title in the log, then add a proxy |
| `/health` `failed` rising | Chromium is crashing; check `docker stats` and the PID count before blaming the site |
| `/health` `truncated` rising | `SCRAPE_BUDGET_S` is biting; too many brands overlapping on the semaphore |
| `/health` `not_found` rising across many brands | either the brand list is bad or `clean_domain` is rejecting a new input shape; a real missing page is one brand at a time |
| `docker stats` memory near 3g | raise `mem_limit` before raising `MAX_CONCURRENCY`, never the other way round |

## Cutting the production workflow over

Not applied, and not to be applied without a decision. Recorded so the shape of the change is known. It is limited to the three HTTP Request nodes; keep their names and their three inputs into `Merge Trustpilot Bands`.

| Node | Change |
|---|---|
| `Apify: Trustpilot 1 Star` | URL → `http://trustpilot-reviews:8000/trustpilot`; authentication → the `trustpilot-scraper` Header Auth credential; drop the `maxTotalChargeUsd` and `timeout` query parameters. Body unchanged. |
| `Apify: Trustpilot Mid` | same |
| `Apify: Trustpilot Positive` | same |

`Assess Vendors` gates on the Apify monthly limit; once Trustpilot and Reddit both leave Apify that gate protects nothing.

## CI

`.github/workflows/ci.yml` runs on pushes to `main`, pull requests and manual dispatch:

- **unit-tests**: `uv run pytest -q` on Python 3.11.
- **docker-image**: builds the image, starts it with a token minted for that run only, and checks `/health`, that a missing token is `401`, a live scrape of gymshark.com (three-star band, max 5, twelve months) that must return rated reviews with a TrustScore, and that an unknown domain is a `404` whose body says `not found`. A `503` fails the build: unlike the Reddit sibling there is no allowance for being blocked, and every run since 16 Sep 2026 has passed.
