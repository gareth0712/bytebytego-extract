"""Extract ByteByteGo engineering visual guides from the public GitHub repo.

No authentication required — guides are public on GitHub.

Usage:
    python guides_main.py --category api-web-development --output-dir output/engineering-visual-guides
    python guides_main.py --all --output-dir output/engineering-visual-guides
"""

import argparse
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

from fetcher import ContentBlock, PageContent
from guides_converter import markdown_to_blocks
from guides_fetcher import (
    fetch_guide_markdown,
    fetch_image,
    list_guide_entries,
    parse_frontmatter,
    sanitize_slug_for_filesystem,
)
from markdown_converter import blocks_to_markdown
from pdf_exporter import save_pdf

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ALL_CATEGORIES: list[str] = [
    "ai-machine-learning",
    "api-web-development",
    "caching-performance",
    "cloud-distributed-systems",
    "computer-fundamentals",
    "database-and-storage",
    "devops-cicd",
    "devtools-productivity",
    "how-it-works",
    "payment-and-fintech",
    "real-world-case-studies",
    "security",
    "software-architecture",
    "software-development",
    "technical-interviews",
]

MULTI_CATEGORY_LOG_NAME = "_multi-category.log"


# ---------------------------------------------------------------------------
# Filename helpers
# ---------------------------------------------------------------------------

def _safe_filename(slug: str) -> str:
    """Convert a guide slug into a safe filename stem (no extension)."""
    return sanitize_slug_for_filesystem(slug)


def _sanitize_title(title: str) -> str:
    """Remove Windows-forbidden characters from a title."""
    sanitized = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "", title)
    sanitized = re.sub(r"\s+", " ", sanitized).strip()
    return sanitized


# ---------------------------------------------------------------------------
# Core extraction
# ---------------------------------------------------------------------------

def extract_guide(
    slug: str,
    output_root: Path,
    multi_category_log: Path,
    prefetched_raw_md: str | None = None,
) -> None:
    """Fetch one guide, convert, and write .md + .pdf into every category folder.

    Args:
        slug: Guide slug as returned by ``list_guide_entries()``.
        output_root: Root output directory (e.g. ``output/engineering-visual-guides``).
        multi_category_log: Path to the multi-category log file.
        prefetched_raw_md: If provided, use this raw markdown instead of fetching again.
    """
    if prefetched_raw_md is not None:
        print(f"  Processing guide: {slug}")
        raw_md = prefetched_raw_md
    else:
        print(f"  Fetching guide: {slug}")
        raw_md = fetch_guide_markdown(slug)
    meta, body = parse_frontmatter(raw_md)

    title: str = meta.get("title", slug)
    image_url: str = meta.get("image", "") or ""
    categories: list[str] = meta.get("categories", []) or []
    draft: bool = bool(meta.get("draft", False))
    description: str = meta.get("description", "") or ""

    if draft:
        print(f"  [skip] Draft guide: {slug}")
        return

    if not categories:
        print(f"  [warn] No categories for guide: {slug} — skipping")
        return

    safe_stem = _safe_filename(slug)

    # Log multi-category guides
    if len(categories) > 1:
        log_line = f"{slug} -> {', '.join(categories)}\n"
        with open(multi_category_log, "a", encoding="utf-8") as lf:
            lf.write(log_line)

    for category in categories:
        cat_dir = output_root / category
        cat_dir.mkdir(parents=True, exist_ok=True)
        images_dir = cat_dir / "images"

        # --- Download image if present ---
        local_image_path: str | None = None
        if image_url:
            # Derive image filename from the URL path only (strip query params & fragment)
            parsed_image_url = urlparse(image_url)
            img_filename = sanitize_slug_for_filesystem(
                parsed_image_url.path.rstrip("/").split("/")[-1]
            )
            img_dest = images_dir / img_filename
            success = fetch_image(image_url, img_dest)
            if success:
                # Store a relative path usable from the markdown file
                local_image_path = f"images/{img_filename}"
            else:
                print(f"  [warn] Could not download image for {slug}: {image_url}")

        # --- Convert markdown body to ContentBlocks ---
        # Pass the absolute image URL so the PDF exporter can download it via HTTP.
        # Also pass skip_image_url so the leading body image (same as frontmatter) is not doubled.
        blocks_for_pdf: list[ContentBlock] = markdown_to_blocks(
            body, title, image_url or None, skip_image_url=image_url or None
        )

        # --- Markdown output ---
        # Build blocks with local path for the markdown file.
        blocks_for_md: list[ContentBlock] = markdown_to_blocks(
            body, title, local_image_path, skip_image_url=image_url or None
        )
        md_filename = f"{safe_stem}.md"
        md_path = cat_dir / md_filename
        md_text = blocks_to_markdown(blocks_for_md)
        md_path.write_text(md_text, encoding="utf-8")

        # --- PDF output ---
        # Use absolute URL blocks so _fetch_image() in pdf_exporter can download them.
        # Drop the leading H1 title block: content_to_flowables() already renders
        # page.title as the first flowable, so keeping the H1 block would double it.
        pdf_blocks = blocks_for_pdf
        if pdf_blocks and pdf_blocks[0].tag == "heading" and pdf_blocks[0].level == 1:
            pdf_blocks = pdf_blocks[1:]
        page = PageContent(
            title=_sanitize_title(title),
            chapter_number=safe_stem,
            blocks=pdf_blocks,
        )
        # Avoid save_pdf's filename generation (it would prefix chapter_number).
        pdf_filename = f"{safe_stem}.pdf"
        pdf_path = cat_dir / pdf_filename
        _write_pdf(page, pdf_path)

        print(f"    [{category}] -> {md_filename}, {pdf_filename}")


def _write_pdf(page: PageContent, pdf_path: Path) -> None:
    """Write PDF for a guide page directly to ``pdf_path`` (bypasses filename generation)."""
    from reportlab.lib.pagesizes import A4
    from reportlab.lib.units import mm
    from reportlab.platypus import SimpleDocTemplate

    from pdf_exporter import _build_styles, content_to_flowables  # type: ignore[attr-defined]

    pdf_path.parent.mkdir(parents=True, exist_ok=True)
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
        title=page.title,
    )
    styles = _build_styles()
    story = content_to_flowables(page, styles)
    doc.build(story)


# ---------------------------------------------------------------------------
# Category extraction
# ---------------------------------------------------------------------------

def extract_category(category_slug: str, output_root: Path) -> dict:
    """Extract all non-draft guides in a category.

    Returns a summary dict with keys: ``total``, ``extracted``, ``skipped``,
    ``errors``, ``category``.
    """
    output_root.mkdir(parents=True, exist_ok=True)
    multi_log = output_root / MULTI_CATEGORY_LOG_NAME
    multi_log.unlink(missing_ok=True)

    print(f"Fetching guide tree from GitHub...")
    all_entries = list_guide_entries()
    print(f"  Total guide entries found: {len(all_entries)}")

    # We need to peek at each guide's frontmatter to filter by category.
    # Collect slugs first, then filter.
    total = 0
    extracted = 0
    skipped = 0
    errors = 0

    for entry in all_entries:
        slug = entry["slug"]
        try:
            raw_md = fetch_guide_markdown(slug)
            meta, _ = parse_frontmatter(raw_md)
            guide_categories: list[str] = meta.get("categories", []) or []
            draft: bool = bool(meta.get("draft", False))

            if category_slug not in guide_categories:
                continue

            total += 1
            if draft:
                print(f"  [skip] Draft: {slug}")
                skipped += 1
                continue

            # Pass prefetched raw_md to avoid fetching twice
            extract_guide(slug, output_root, multi_log, prefetched_raw_md=raw_md)
            extracted += 1

        except Exception as exc:
            print(f"  [error] {slug}: {exc}", file=sys.stderr)
            errors += 1
            # Don't stop — continue to next guide

    return {
        "category": category_slug,
        "total": total,
        "extracted": extracted,
        "skipped": skipped,
        "errors": errors,
    }


def extract_all_categories(output_root: Path) -> dict:
    """Extract every non-draft guide into every category it belongs to.

    Each guide is fetched once and written to all its category folders.

    Returns a summary dict.
    """
    output_root.mkdir(parents=True, exist_ok=True)
    multi_log = output_root / MULTI_CATEGORY_LOG_NAME
    multi_log.unlink(missing_ok=True)

    print("Fetching guide tree from GitHub...")
    all_entries = list_guide_entries()
    print(f"  Total guide entries found: {len(all_entries)}")

    total = len(all_entries)
    extracted = 0
    skipped = 0
    errors = 0

    for idx, entry in enumerate(all_entries, start=1):
        slug = entry["slug"]
        print(f"\n[{idx}/{total}] {slug}")
        try:
            raw_md = fetch_guide_markdown(slug)
            meta, _ = parse_frontmatter(raw_md)
            draft: bool = bool(meta.get("draft", False))

            if draft:
                print(f"  [skip] Draft")
                skipped += 1
                continue

            # Pass prefetched raw_md to avoid fetching twice
            extract_guide(slug, output_root, multi_log, prefetched_raw_md=raw_md)
            extracted += 1

        except Exception as exc:
            print(f"  [error] {slug}: {exc}", file=sys.stderr)
            errors += 1

    return {
        "total": total,
        "extracted": extracted,
        "skipped": skipped,
        "errors": errors,
    }


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Extract ByteByteGo engineering visual guides to Markdown and PDF."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "--category",
        metavar="SLUG",
        help=(
            "Extract only guides in this category. "
            f"Valid values: {', '.join(ALL_CATEGORIES)}"
        ),
    )
    group.add_argument(
        "--all",
        action="store_true",
        help="Extract all guides from all categories.",
    )
    parser.add_argument(
        "--output-dir", "-o",
        default="output/engineering-visual-guides",
        help="Root output directory (default: output/engineering-visual-guides)",
    )
    args = parser.parse_args()

    output_root = Path(args.output_dir)

    if args.all:
        summary = extract_all_categories(output_root)
        print("\n" + "=" * 60)
        print("SUMMARY (all categories)")
        print(f"  Total guides:    {summary['total']}")
        print(f"  Extracted:       {summary['extracted']}")
        print(f"  Skipped (draft): {summary['skipped']}")
        print(f"  Errors:          {summary['errors']}")
        print("=" * 60)
    else:
        category = args.category
        if category not in ALL_CATEGORIES:
            print(
                f"Unknown category: {category!r}. "
                f"Valid: {', '.join(ALL_CATEGORIES)}",
                file=sys.stderr,
            )
            sys.exit(1)
        print(f"Extracting category: {category}")
        print(f"Output dir: {output_root}")
        summary = extract_category(category, output_root)
        print("\n" + "=" * 60)
        print(f"SUMMARY: {category}")
        print(f"  Guides in category: {summary['total']}")
        print(f"  Extracted:          {summary['extracted']}")
        print(f"  Skipped (draft):    {summary['skipped']}")
        print(f"  Errors:             {summary['errors']}")
        print("=" * 60)


if __name__ == "__main__":
    main()
