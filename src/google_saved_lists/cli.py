"""Command-line interface for the Google saved-list scraper."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from google_saved_lists.scraper import scrape_saved_list


def build_parser() -> argparse.ArgumentParser:
    """Build the CLI argument parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("url", help="Google Maps saved-list URL")
    parser.add_argument("--output", type=Path, help="Optional JSON output path")
    parser.add_argument(
        "--headed",
        action="store_true",
        help="Run the browser in headed mode for debugging.",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=30_000,
        help="Navigation timeout in milliseconds.",
    )
    parser.add_argument(
        "--settle-ms",
        type=int,
        default=3_000,
        help="Extra wait time after the page loads.",
    )
    return parser


def main() -> int:
    """Run the CLI."""
    parser = build_parser()
    args = parser.parse_args()

    result = scrape_saved_list(
        args.url,
        headless=not args.headed,
        timeout_ms=args.timeout_ms,
        settle_time_ms=args.settle_ms,
    )
    payload = json.dumps(result.to_dict(), indent=2, ensure_ascii=False)

    if args.output is not None:
        args.output.write_text(f"{payload}\n", encoding="utf-8")
    else:
        print(payload)
    return 0
