"""Convert parsed page content to a Markdown file."""

import re
from pathlib import Path

from fetcher import ContentBlock, PageContent


def _sanitize_filename(name: str) -> str:
    """Remove characters not allowed in filenames."""
    # Replace characters that are invalid in Windows filenames
    sanitized = re.sub(r'[<>:"/\\|?*]', '', name)
    # Collapse multiple spaces
    sanitized = re.sub(r'\s+', ' ', sanitized).strip()
    return sanitized


def _table_html_to_md(html_str: str) -> str:
    """Convert an HTML table string to Markdown table format."""
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(html_str, "html.parser")
    table = soup.find("table")
    if not table:
        return html_str

    rows = table.find_all("tr")
    if not rows:
        return html_str

    md_rows = []
    for row in rows:
        cells = row.find_all(["th", "td"])
        md_row = "| " + " | ".join(c.get_text().strip() for c in cells) + " |"
        md_rows.append(md_row)

    if len(md_rows) >= 1:
        # Insert separator after first row (header)
        num_cols = md_rows[0].count("|") - 1
        separator = "| " + " | ".join(["---"] * num_cols) + " |"
        md_rows.insert(1, separator)

    return "\n".join(md_rows)


def blocks_to_markdown(blocks: list[ContentBlock], title: str = "") -> str:
    """Convert a list of ContentBlocks to Markdown text."""
    lines: list[str] = []

    # Add page title as h1 if provided
    if title:
        lines.append(f"# {title}")
        lines.append("")

    for block in blocks:
        if block.tag == "chapter-number":
            # Render the large chapter number as bold text before the title heading
            lines.append(f"**Chapter {block.text}**")
            lines.append("")

        elif block.tag == "heading":
            prefix = "#" * block.level
            lines.append(f"{prefix} {block.text}")
            lines.append("")

        elif block.tag == "p":
            lines.append(block.text)
            lines.append("")

        elif block.tag == "img":
            alt = block.alt or "image"
            lines.append(f"![{alt}]({block.src})")
            lines.append("")

        elif block.tag in ("ul", "ol"):
            for i, item in enumerate(block.children):
                if block.tag == "ol":
                    lines.append(f"{i + 1}. {item}")
                else:
                    lines.append(f"- {item}")
            lines.append("")

        elif block.tag == "pre":
            lang = block.language or ""
            lines.append(f"```{lang}")
            lines.append(block.text)
            lines.append("```")
            lines.append("")

        elif block.tag == "blockquote":
            for bq_line in block.text.split("\n"):
                lines.append(f"> {bq_line}")
            lines.append("")

        elif block.tag == "table":
            if block.headers or block.children:
                # Structured table from parsed JSX
                if block.headers:
                    lines.append("| " + " | ".join(block.headers) + " |")
                    lines.append("| " + " | ".join(["---"] * len(block.headers)) + " |")
                for row in block.children:
                    lines.append("| " + " | ".join(row) + " |")
            elif block.text:
                lines.append(_table_html_to_md(block.text))
            lines.append("")

        elif block.tag == "hr":
            lines.append("---")
            lines.append("")

        elif block.tag == "info-box":
            # GitHub-flavored markdown admonition syntax
            # > [!TIP]  /  > [!NOTE]  /  > [!WARNING]
            admon = block.admonition_type.upper() or "NOTE"
            lines.append(f"> [!{admon}]")
            for text_line in block.text.split("\n"):
                lines.append(f"> {text_line}")
            lines.append("")

        elif block.tag == "sample-dialogue":
            # Render as a blockquote with speaker lines
            for child_line in block.children:
                lines.append(f"> {child_line}")
                lines.append(">")
            # Remove trailing empty blockquote marker
            if lines and lines[-1] == ">":
                lines[-1] = ""
            else:
                lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def generate_filename(page: PageContent) -> str:
    """Generate the markdown filename from chapter number and title.

    Handles both int chapter numbers (OOD course: 1 → "01") and
    string chapter numbers (Coding Patterns course: "01-00" → "01-00").
    """
    num = str(page.chapter_number) if isinstance(page.chapter_number, str) else f"{page.chapter_number:02d}"
    title = _sanitize_filename(page.title)
    return f"{num}. {title}.md"


def save_markdown(page: PageContent, output_dir: str | Path) -> Path:
    """Convert PageContent to markdown and save to a file.

    Returns the path to the saved file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    md_text = blocks_to_markdown(page.blocks, title=page.title)
    filename = generate_filename(page)
    filepath = output_dir / filename

    filepath.write_text(md_text, encoding="utf-8")
    return filepath
