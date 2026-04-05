"""ByteByteGo course page extractor.

Fetches a ByteByteGo course page, extracts the main content,
and exports it as Markdown and PDF.

Usage:
    python __init__.py <url> [--output-dir DIR] [--cookies FILE] [--dump-json]

Example:
    python __init__.py https://bytebytego.com/courses/object-oriented-design-interview/what-is-an-object-oriented-design-interview
"""

import argparse
import json
import os
import re
from pathlib import Path

import fetcher
from fetcher import fetch_page, parse_content
from markdown_converter import save_markdown
from pdf_exporter import save_pdf


def extract(
    url: str,
    output_dir: str | Path = ".",
    cookies: dict[str, str] | None = None,
    dump_json: bool = False,
) -> dict[str, Path]:
    """Extract content from a ByteByteGo page and save as MD + PDF.

    Args:
        url: Full URL to a ByteByteGo course page.
        output_dir: Directory to save output files.
        cookies: Optional dict of cookies for authenticated access.
        dump_json: If True, save raw pageProps JSON alongside the outputs.

    Returns:
        Dict with 'markdown' and 'pdf' keys pointing to saved file paths.
    """
    output_dir = Path(output_dir)

    print(f"Fetching: {url}")
    html = fetch_page(url, cookies=cookies)

    if dump_json:
        output_dir.mkdir(parents=True, exist_ok=True)
        data = fetcher._extract_next_data(html)
        page_props = data["props"]["pageProps"]
        slug = url.rstrip("/").split("/")[-1]
        json_path = output_dir / f"{slug}.json"
        json_path.write_text(
            json.dumps(page_props, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"  JSON saved: {json_path}")

    print("Parsing content...")
    page = parse_content(html)
    print(f"  Title: {page.title}")
    print(f"  Chapter: {page.chapter_number}")
    print(f"  Content blocks: {len(page.blocks)}")

    print("Saving Markdown...")
    md_path = save_markdown(page, output_dir)
    print(f"  -> {md_path}")

    print("Generating PDF...")
    pdf_path = save_pdf(page, output_dir)
    print(f"  -> {pdf_path}")

    print("Done!")
    return {"markdown": md_path, "pdf": pdf_path}


def main():
    parser = argparse.ArgumentParser(
        description="Extract ByteByteGo course pages to Markdown and PDF."
    )
    parser.add_argument("url", help="ByteByteGo course page URL")
    parser.add_argument(
        "--output-dir", "-o",
        default=".",
        help="Output directory (default: current directory)",
    )
    parser.add_argument(
        "--cookies", "-c",
        default=None,
        help=(
            "Path to Netscape-format cookie file for authenticated access. "
            "Default: auto-loads from S:/program-files/cookies/cookies-bytebytego.txt "
            "or BYTEBYTEGO_COOKIES env var."
        ),
    )
    parser.add_argument(
        "--dump-json",
        action="store_true",
        help="Save raw pageProps JSON alongside MD/PDF outputs.",
    )
    args = parser.parse_args()

    # Resolve cookie path: CLI arg > env var > default
    cookies = None
    cookie_path = args.cookies or os.environ.get("BYTEBYTEGO_COOKIES")
    if cookie_path:
        fetcher.COOKIE_PATH = cookie_path
    # Cookies are loaded automatically by fetch_page via _load_cookies()

    extract(args.url, args.output_dir, dump_json=args.dump_json)


if __name__ == "__main__":
    main()
