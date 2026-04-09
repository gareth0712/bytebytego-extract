"""ByteByteGo course page extractor.

Fetches a ByteByteGo course page, extracts the main content,
and exports it as Markdown and PDF.

Usage:
    python __main__.py <url> [--output-dir DIR] [--cookies FILE] [--dump-json]
    python __main__.py <url> --all [--output-dir DIR] [--cookies FILE] [--dump-json]

Example:
    python __main__.py https://bytebytego.com/courses/object-oriented-design-interview/what-is-an-object-oriented-design-interview
    python __main__.py https://bytebytego.com/courses/object-oriented-design-interview/what-is-an-object-oriented-design-interview --all
"""

import argparse
import json
import os
import sys
from pathlib import Path

import fetcher
from fetcher import (
    fetch_page,
    parse_content,
    extract_toc,
    build_chapter_url,
    derive_course_base_url,
)
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


def extract_all(
    seed_url: str,
    output_dir: str | Path = ".",
    dump_json: bool = False,
) -> None:
    """Extract all chapters of a course using the TOC from the seed URL.

    Fetches the seed URL first to get the TOC, then processes each chapter
    sequentially. Stops immediately on any error so parser issues can be fixed
    before continuing.

    Args:
        seed_url: Any chapter URL from the course (used to discover the TOC).
        output_dir: Directory to save all output files.
        dump_json: If True, save raw pageProps JSON alongside MD/PDF outputs.
    """
    output_dir = Path(output_dir)

    print(f"Fetching seed page to discover TOC: {seed_url}")
    html = fetch_page(seed_url)

    toc = extract_toc(html)
    if not toc:
        print(
            "ERROR: No TOC found in page. The page may be paywalled or the URL is invalid.",
            file=sys.stderr,
        )
        sys.exit(1)

    base_url = derive_course_base_url(seed_url)
    course_slug = base_url.rstrip("/").split("/")[-1]
    output_dir = output_dir / course_slug
    total = len(toc)
    print(f"Found {total} chapters in TOC. Base URL: {base_url}")
    print(f"Output directory: {output_dir}")
    print("-" * 60)

    results: list[dict] = []
    for idx, entry in enumerate(toc, start=1):
        title = entry.get("title", "Unknown")
        chapter_url = build_chapter_url(base_url, entry)

        print(f"\n[{idx}/{total}] Extracting: {title}")
        print(f"  URL: {chapter_url}")

        try:
            paths = extract(chapter_url, output_dir, dump_json=dump_json)
            results.append({"index": idx, "title": title, "status": "OK", "paths": paths})
        except ValueError as exc:
            # Empty pageProps means cookies are expired or chapter is paywalled
            print(f"\nERROR: {exc}", file=sys.stderr)
            print(
                "Stopping. Re-export your cookies from the browser and retry.",
                file=sys.stderr,
            )
            sys.exit(1)
        except Exception as exc:
            print(f"\nERROR on chapter {idx} ({title}): {exc}", file=sys.stderr)
            print("Stopping to allow parser fix before continuing.", file=sys.stderr)
            sys.exit(1)

    # Summary table
    print("\n" + "=" * 60)
    print(f"SUMMARY: {len(results)}/{total} chapters extracted successfully")
    print("=" * 60)
    for r in results:
        md = r["paths"]["markdown"].name
        print(f"  [{r['index']:>3}/{total}] {r['title']}")
        print(f"         -> {md}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract ByteByteGo course pages to Markdown and PDF."
    )
    parser.add_argument("url", help="ByteByteGo course page URL")
    parser.add_argument(
        "--all",
        action="store_true",
        help=(
            "Extract all chapters of the course. The provided URL is used only "
            "to discover the TOC; all chapters are then extracted sequentially."
        ),
    )
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
    cookie_path = args.cookies or os.environ.get("BYTEBYTEGO_COOKIES")
    if cookie_path:
        fetcher.COOKIE_PATH = cookie_path
    # Cookies are loaded automatically by fetch_page via _load_cookies()

    if args.all:
        extract_all(args.url, args.output_dir, dump_json=args.dump_json)
    else:
        extract(args.url, args.output_dir, dump_json=args.dump_json)


if __name__ == "__main__":
    main()
