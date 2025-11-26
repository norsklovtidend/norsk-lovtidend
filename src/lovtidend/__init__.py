"""Command line entry-point for the Lovtidend scraper."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from urllib.parse import parse_qs, urlencode, urlparse, urlunparse

from .scraper import DEFAULT_BASE_URL, LovtidendScraper


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download XML versions of documents listed in Norsk Lovtidend.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("data/xml"),
        help="Directory where XML files will be written (default: data/xml)",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=None,
        help="Maximum number of register pages to crawl. Default is unlimited.",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=None,
        help="Limit the total number of documents processed. Default is unlimited.",
    )
    parser.add_argument(
        "--offset",
        type=int,
        default=None,
        help="Start scraping from a given offset (multiples of 20).",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Always re-download XML files even if they already exist.",
    )
    parser.add_argument(
        "--base-url",
        default=DEFAULT_BASE_URL,
        help="Base URL for the Lovtidend register (for advanced usage).",
    )
    parser.add_argument(
        "--start-url",
        default=None,
        help="Explicit URL to start from. Overrides --offset if provided.",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=1982,
        help="First year to scrape (default: 1982).",
    )
    parser.add_argument(
        "--end-year",
        type=int,
        default=None,
        help="Last year to scrape (default: current year).",
    )
    parser.add_argument(
        "--checkpoint-file",
        type=Path,
        default=Path("data/lovtidend_checkpoint.json"),
        help="Where to store progress for resume support (default: data/lovtidend_checkpoint.json)",
    )
    parser.add_argument(
        "--no-resume",
        action="store_true",
        help="Ignore checkpoint files and start from the requested offset.",
    )
    return parser


def make_start_url(
    base_url: str,
    start_url: str | None,
    offset: int | None,
    year: int | None,
) -> str | None:
    if start_url:
        return start_url
    if offset is None and year is None:
        return None
    parsed = urlparse(base_url)
    query = parse_qs(parsed.query)
    query.setdefault("avdeling", ["*"])
    query.setdefault("ministry", ["*"])
    query.setdefault("kunngjortDato", ["*"])
    query.setdefault("search", [""])
    if year is not None:
        query["year"] = [str(year)]
    if offset is not None:
        query["offset"] = [str(offset)]
    encoded = urlencode(query, doseq=True)
    return urlunparse(parsed._replace(query=encoded))


def main(argv: Sequence[str] | None = None) -> None:
    parser = build_parser()
    args = parser.parse_args(list(argv) if argv is not None else None)
    start_url = make_start_url(args.base_url, args.start_url, args.offset, args.start_year)

    with LovtidendScraper(
        args.output,
        base_url=args.base_url,
        overwrite=args.overwrite,
    ) as scraper:
        scraper.run(
            start_url=start_url,
            max_pages=args.max_pages,
            limit=args.limit,
            checkpoint_path=args.checkpoint_file,
            no_resume=args.no_resume,
            start_year=args.start_year,
            end_year=args.end_year,
        )


__all__ = [
    "main",
    "build_parser",
    "make_start_url",
]
