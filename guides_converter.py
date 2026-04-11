"""Convert a guide's markdown body into ContentBlock objects.

Uses a hand-rolled CommonMark-subset parser covering:
  - # through ###### headings
  - Paragraphs (with **bold**, *italic*, `code`, [link](url) inline)
  - ![alt](url) images
  - Fenced code blocks (``` ... ```)
  - - / * unordered lists and 1. ordered lists
  - --- horizontal rules
  - > blockquotes
  - Blank-line separation

No external markdown library required.
"""

import re
from pathlib import Path

from fetcher import ContentBlock


# ---------------------------------------------------------------------------
# Inline formatting helpers
# ---------------------------------------------------------------------------

def _process_inline(text: str) -> str:
    """Convert inline markdown to a plain-text-friendly representation.

    Keeps **bold**, *italic*, `code` as-is (they render fine in markdown output).
    Converts [text](url) links to just the text (since PDF can't render hyperlinks).
    """
    # Convert [text](url) → text (drop the URL for clean rendering)
    text = re.sub(r'\[([^\]]+)\]\([^)]+\)', r'\1', text)
    return text


# ---------------------------------------------------------------------------
# Fenced code block detection
# ---------------------------------------------------------------------------

_FENCE_RE = re.compile(r'^(`{3,}|~{3,})(\w*)\s*$')


# ---------------------------------------------------------------------------
# Main converter
# ---------------------------------------------------------------------------

def markdown_to_blocks(
    body_md: str,
    title: str,
    local_image_path: str | None,
    skip_image_url: str | None = None,
) -> list[ContentBlock]:
    """Convert guide markdown body into a list of ContentBlock objects.

    Always prepends:
      1. A ``heading`` level-1 block with ``title``.
      2. An ``img`` block (if ``local_image_path`` is provided).

    Then parses the body line-by-line, skipping any standalone image whose URL
    matches ``skip_image_url`` (used to deduplicate the frontmatter image).

    Args:
        body_md: Markdown body text (everything after frontmatter).
        title: Guide title from frontmatter.
        local_image_path: Local relative path for the featured image (for MD output),
            or absolute URL (for PDF output). None if no image.
        skip_image_url: If set, standalone images with this URL are silently skipped.
    """
    blocks: list[ContentBlock] = []

    # --- Title block ---
    blocks.append(ContentBlock(tag="heading", level=1, text=title))

    # --- Featured image block (from frontmatter) ---
    if local_image_path:
        blocks.append(ContentBlock(tag="img", src=local_image_path, alt=title))

    lines = body_md.splitlines()
    i = 0
    n = len(lines)

    # Buffer for paragraph accumulation
    para_lines: list[str] = []

    def flush_paragraph() -> None:
        if para_lines:
            text = " ".join(line for line in para_lines if line)
            text = _process_inline(text.strip())
            if text:
                blocks.append(ContentBlock(tag="p", text=text))
            para_lines.clear()

    while i < n:
        line = lines[i]

        # --- Blank line: flush paragraph ---
        if line.strip() == "":
            flush_paragraph()
            i += 1
            continue

        # --- Fenced code block ---
        fence_match = _FENCE_RE.match(line)
        if fence_match:
            flush_paragraph()
            fence_char = fence_match.group(1)
            language = fence_match.group(2)
            i += 1
            code_lines: list[str] = []
            while i < n:
                if lines[i].startswith(fence_char):
                    i += 1
                    break
                code_lines.append(lines[i])
                i += 1
            blocks.append(
                ContentBlock(
                    tag="pre",
                    text="\n".join(code_lines),
                    language=language,
                )
            )
            continue

        # --- ATX headings (#, ##, ...) ---
        heading_match = re.match(r'^(#{1,6})\s+(.*)', line)
        if heading_match:
            flush_paragraph()
            level = len(heading_match.group(1))
            text = _process_inline(heading_match.group(2).strip())
            blocks.append(ContentBlock(tag="heading", level=level, text=text))
            i += 1
            continue

        # --- Horizontal rule (--- or *** or ___ with optional spaces) ---
        if re.match(r'^(\s*[-*_]){3,}\s*$', line) and line.strip():
            flush_paragraph()
            # Only treat as HR if all chars are the same separator
            stripped = line.strip().replace(" ", "")
            if len(set(stripped)) == 1 and stripped[0] in ("-", "*", "_"):
                blocks.append(ContentBlock(tag="hr"))
                i += 1
                continue

        # --- Blockquote (> ...) ---
        if line.startswith(">"):
            flush_paragraph()
            bq_lines: list[str] = []
            while i < n and lines[i].startswith(">"):
                bq_lines.append(lines[i].lstrip(">").strip())
                i += 1
            text = "\n".join(bq_lines)
            text = _process_inline(text)
            blocks.append(ContentBlock(tag="blockquote", text=text))
            continue

        # --- Unordered list (- item or * item) ---
        if re.match(r'^(\s*[-*+])\s+', line):
            flush_paragraph()
            items: list[str] = []
            while i < n:
                ul_match = re.match(r'^\s*[-*+]\s+(.*)', lines[i])
                if ul_match:
                    items.append(_process_inline(ul_match.group(1).strip()))
                    i += 1
                elif items and lines[i].startswith("  "):
                    # Continuation of last item (indented)
                    items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            if items:
                blocks.append(ContentBlock(tag="ul", children=items))
            continue

        # --- Ordered list (1. item) ---
        if re.match(r'^\s*\d+\.\s+', line):
            flush_paragraph()
            ol_items: list[str] = []
            while i < n:
                ol_match = re.match(r'^\s*\d+\.\s+(.*)', lines[i])
                if ol_match:
                    ol_items.append(_process_inline(ol_match.group(1).strip()))
                    i += 1
                elif ol_items and lines[i].startswith("   "):
                    # Continuation
                    ol_items[-1] += " " + lines[i].strip()
                    i += 1
                else:
                    break
            if ol_items:
                blocks.append(ContentBlock(tag="ol", children=ol_items))
            continue

        # --- Standalone image: ![alt](url) ---
        img_match = re.match(r'^!\[([^\]]*)\]\(([^)]+)\)\s*$', line)
        if img_match:
            flush_paragraph()
            alt = img_match.group(1)
            src = img_match.group(2)
            # Skip if this is the same image as the frontmatter image (already emitted)
            if skip_image_url and src == skip_image_url:
                i += 1
                continue
            blocks.append(ContentBlock(tag="img", src=src, alt=alt))
            i += 1
            continue

        # --- Setext-style heading (underlined with == or --) ---
        if i + 1 < n:
            next_line = lines[i + 1]
            if re.match(r'^=+\s*$', next_line):
                flush_paragraph()
                blocks.append(
                    ContentBlock(tag="heading", level=1, text=_process_inline(line.strip()))
                )
                i += 2
                continue
            if re.match(r'^-+\s*$', next_line) and not re.match(r'^(\s*[-*+])\s+', line):
                flush_paragraph()
                blocks.append(
                    ContentBlock(tag="heading", level=2, text=_process_inline(line.strip()))
                )
                i += 2
                continue

        # --- Default: accumulate as paragraph ---
        para_lines.append(line.strip())
        i += 1

    flush_paragraph()
    return blocks
