"""Interactively inspect what the SEC pipeline returns for a ticker.

Examples:
    python inspect_filing.py                     # prompts for a ticker
    python inspect_filing.py AAPL
    python inspect_filing.py MSFT --form 10-K --report-year 2025 --chars 3000
    python inspect_filing.py AAPL --chunks 3 --chunk-size 800
    python inspect_filing.py --file dump\\AAPL_10-K_0000320193-25-000079.htm --full
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from document_extractor import ExtractionError, extract_document
from filing_resolver import ResolverError
from sec_client import SecError
from sec_pipeline import DUMP_DIR, fetch_filing


def _utf8_stdout() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def parse_args(argv=None):
    parser = argparse.ArgumentParser(
        description="Fetch a SEC filing and show the extracted text.",
    )
    parser.add_argument(
        "ticker", nargs="?", help="Ticker symbol, e.g. AAPL (prompted if omitted)"
    )
    parser.add_argument(
        "--form", default="10-K", help="SEC form type (default: 10-K)"
    )
    parser.add_argument(
        "--report-year",
        type=int,
        default=None,
        help="Match the fiscal period year (reportDate), e.g. 2025",
    )
    parser.add_argument(
        "--filing-year",
        type=int,
        default=None,
        help="Match the calendar submission year (filingDate)",
    )
    parser.add_argument(
        "--file",
        type=Path,
        default=None,
        help="Extract from an existing dumped file instead of fetching",
    )
    parser.add_argument(
        "--chars",
        type=int,
        default=2000,
        help="Preview length in characters (default: 2000)",
    )
    parser.add_argument(
        "--chunks",
        type=int,
        default=0,
        help="Print this many sample chunk windows",
    )
    parser.add_argument(
        "--chunk-size",
        type=int,
        default=1000,
        help="Sample chunk size in characters (default: 1000)",
    )
    parser.add_argument(
        "--full", action="store_true", help="Print the entire extracted text"
    )
    parser.add_argument(
        "--save-text",
        action="store_true",
        help="Also save the extracted text next to the dump as *_extracted.txt",
    )
    return parser.parse_args(argv)


def show_text(text: str, args) -> None:
    total = len(text)
    if args.full:
        print(f"=== Full extracted text ({total} chars) ===")
        print(text)
    else:
        preview = text[: args.chars]
        print(f"=== Extracted text preview (first {len(preview)} of {total} chars) ===")
        print(preview)
        if total > len(preview):
            print(f"\n... [{total - len(preview)} more chars; use --full to print all]")

    if args.chunks > 0:
        size = max(1, args.chunk_size)
        print(f"\n=== Sample chunks ({args.chunks} x {size} chars) ===")
        for index in range(args.chunks):
            start = index * size
            if start >= total:
                break
            window = text[start : start + size]
            print(f"\n--- chunk {index + 1} [{start}:{start + len(window)}] ---")
            print(window)


def save_text(text: str, dump_path: Path) -> Path:
    out = dump_path.with_name(dump_path.stem + "_extracted.txt")
    out.write_text(text, encoding="utf-8")
    return out


def run_fetch(args, ticker: str) -> None:
    result = fetch_filing(
        ticker,
        args.form,
        report_year=args.report_year,
        filing_year=args.filing_year,
        dump_dir=DUMP_DIR,
    )
    print("=== Filing ===")
    print(f"ticker       : {result.ticker}")
    print(f"cik          : {result.cik}")
    print(f"company      : {result.company}")
    print(f"form         : {result.form_type}")
    print(f"accession    : {result.accession_number}")
    print(f"filing_date  : {result.filing_date}")
    print(f"report_date  : {result.report_date}")
    print(f"document     : {result.primary_document}")
    print(f"source_url   : {result.source_url}")
    print(f"content_type : {result.content_type}")
    print(f"title        : {result.title}")
    print(f"raw_bytes    : {len(result.raw_content)}")
    print(f"text_length  : {len(result.extracted_text)}")
    print(f"dump_path    : {result.dump_path}")

    if args.save_text:
        saved = save_text(result.extracted_text, Path(result.dump_path))
        print(f"saved_text   : {saved}")

    print()
    show_text(result.extracted_text, args)


def run_from_file(args) -> None:
    path: Path = args.file
    if not path.exists():
        raise FileNotFoundError(f"Dump file not found: {path}")

    content_type = "application/xml" if path.suffix.lower() == ".xml" else "text/html"
    result = extract_document(path.read_bytes(), content_type, str(path))
    print("=== Dumped file ===")
    print(f"file         : {path}")
    print(f"content_type : {result.content_type}")
    print(f"title        : {result.title}")
    print(f"text_length  : {len(result.text)}")

    if args.save_text:
        saved = save_text(result.text, path)
        print(f"saved_text   : {saved}")

    print()
    show_text(result.text, args)


def main(argv=None) -> int:
    args = parse_args(argv)
    _utf8_stdout()

    try:
        if args.file:
            run_from_file(args)
            return 0

        if args.ticker:
            run_fetch(args, args.ticker)
            return 0

        while True:
            try:
                ticker = input("Ticker (blank to quit): ").strip()
            except (EOFError, KeyboardInterrupt):
                print()
                return 0
            if not ticker:
                return 0
            print()
            try:
                run_fetch(args, ticker)
            except (ResolverError, SecError, ExtractionError) as exc:
                print(f"ERROR: {type(exc).__name__}: {exc}")
            print()
    except FileNotFoundError as exc:
        print(f"ERROR: {exc}")
        return 1
    except (ResolverError, SecError, ExtractionError) as exc:
        print(f"ERROR: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
