"""Self-hosted Trustpilot review scraper, a drop-in for the Apify actor used by the Stage 4 workflow."""

import argparse
import json
import logging
import sys

__version__ = "0.1.0"


def _cmd_serve(args: argparse.Namespace) -> int:
    import uvicorn

    from .config import settings

    uvicorn.run(
        "trustpilot_reviews.api:app",
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level="info",
    )
    return 0


def _cmd_scrape(args: argparse.Namespace) -> int:
    from .mapping import to_items
    from .scraper import TrustpilotError, scrape

    stars = [int(s) for s in args.stars.split(",") if s.strip()]
    try:
        result = scrape(args.domain, stars, args.max, args.months)
    except TrustpilotError as e:
        print(json.dumps({"error": {"type": type(e).__name__, "status": e.status, "message": str(e)}}), file=sys.stderr)
        return 2
    payload = {
        "domain": result.domain,
        "seconds": result.seconds,
        "pages_fetched": result.pages_fetched,
        "total_available": result.total_count,
        "company": {
            "name": result.business_unit.display_name,
            "trust_score": result.business_unit.trust_score,
            "total_reviews": result.business_unit.number_of_reviews,
        },
        "items": to_items(result),
    }
    # ensure_ascii keeps this safe on a cp1252 Windows console.
    print(json.dumps(payload, indent=2 if args.pretty else None, ensure_ascii=True))
    return 0


def main(argv: list[str] | None = None) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    parser = argparse.ArgumentParser(prog="trustpilot-reviews")
    sub = parser.add_subparsers(dest="cmd", required=True)

    serve = sub.add_parser("serve", help="run the HTTP service n8n calls")
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)
    serve.set_defaults(func=_cmd_serve)

    scrape_p = sub.add_parser("scrape", help="scrape one brand from the command line and print JSON")
    scrape_p.add_argument("domain")
    scrape_p.add_argument("--stars", default="1,2,3,4,5", help="comma-separated star ratings, e.g. 1,2")
    scrape_p.add_argument("--max", type=int, default=20)
    scrape_p.add_argument("--months", type=int, default=None, help="only reviews from the last N months; default all time, as the API")
    scrape_p.add_argument("--pretty", action="store_true")
    scrape_p.set_defaults(func=_cmd_scrape)

    args = parser.parse_args(argv)
    sys.exit(args.func(args))
