"""Runtime configuration, read once from the environment."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name, "").strip()
    return int(raw) if raw else default


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name, "").strip().lower()
    if not raw:
        return default
    return raw in ("1", "true", "yes", "on")


@dataclass(frozen=True)
class Settings:
    # Shared secret the caller sends as `Authorization: Bearer <token>`. Empty = no auth (local testing only).
    api_token: str = os.environ.get("API_TOKEN", "").strip()
    # Optional proxy URL passed straight to Scrapling.
    proxy: str | None = os.environ.get("SCRAPER_PROXY", "").strip() or None
    # How many browser fetches may run at once. The caller fires three band calls in parallel per brand.
    max_concurrency: int = _env_int("MAX_CONCURRENCY", 3)
    # Per-page browser timeout. The caller's node timeout is 250 s per band call, so keep pages well under that.
    fetch_timeout_ms: int = _env_int("FETCH_TIMEOUT_MS", 30_000)
    # Trustpilot sits behind a CloudFront JS challenge, not Cloudflare. The stealth browser passes it
    # without the Cloudflare solver, but the flag is kept so it can be flipped without a deploy.
    solve_cloudflare: bool = _env_bool("SOLVE_CLOUDFLARE", False)
    # We only ever read __NEXT_DATA__ out of the HTML document, so images, fonts, CSS and media
    # are pure memory and bandwidth cost in the browser. Blocking them is the single biggest
    # memory lever we have. Flip to false if Trustpilot starts challenging the stripped page.
    block_resources: bool = _env_bool("BLOCK_RESOURCES", True)
    # Hard cap on pages per band call regardless of `max` requested. 20 reviews per page.
    max_pages: int = _env_int("MAX_PAGES", 3)
    # Total wall-clock budget for one scrape. Nothing cancels a request once it starts - Starlette
    # does not cancel handlers on client disconnect, and the browser runs in an uncancellable
    # thread - so a caller that gives up does not free the browser. This is what frees it: the
    # scrape stops starting new pages and returns what it has. Must stay under the caller's 250 s node timeout.
    scrape_budget_s: int = _env_int("SCRAPE_BUDGET_S", 200)
    # Pause between pages of the same band call, seconds.
    page_delay_s: float = float(os.environ.get("PAGE_DELAY_S", "2"))
    host: str = os.environ.get("HOST", "0.0.0.0")
    port: int = _env_int("PORT", 8000)


settings = Settings()
