"""Export parsed page content to a styled PDF."""

import io
import re
from pathlib import Path

import requests
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT, TA_CENTER
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch, mm
from reportlab.platypus import (
    BaseDocTemplate,
    Frame,
    HRFlowable,
    Image,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from fetcher import ContentBlock, PageContent


# --- Styles ---

def _build_styles() -> dict[str, ParagraphStyle]:
    """Create custom paragraph styles for the PDF."""
    base = getSampleStyleSheet()

    styles = {
        "title": ParagraphStyle(
            "CustomTitle",
            parent=base["Title"],
            fontSize=24,
            leading=30,
            spaceAfter=20,
            textColor=colors.HexColor("#1a1a2e"),
        ),
        "h1": ParagraphStyle(
            "CustomH1",
            parent=base["Heading1"],
            fontSize=20,
            leading=26,
            spaceBefore=18,
            spaceAfter=10,
            textColor=colors.HexColor("#16213e"),
        ),
        "h2": ParagraphStyle(
            "CustomH2",
            parent=base["Heading2"],
            fontSize=16,
            leading=22,
            spaceBefore=14,
            spaceAfter=8,
            textColor=colors.HexColor("#1a1a2e"),
        ),
        "h3": ParagraphStyle(
            "CustomH3",
            parent=base["Heading3"],
            fontSize=13,
            leading=18,
            spaceBefore=10,
            spaceAfter=6,
            textColor=colors.HexColor("#2d3436"),
        ),
        "body": ParagraphStyle(
            "CustomBody",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            spaceAfter=8,
            textColor=colors.HexColor("#2d3436"),
        ),
        "code": ParagraphStyle(
            "CustomCode",
            parent=base["Normal"],
            fontSize=8.5,
            leading=12,
            fontName="Courier",
            leftIndent=0,
            rightIndent=0,
            spaceAfter=0,
            spaceBefore=0,
            textColor=colors.HexColor("#333333"),
        ),
        "code_block": ParagraphStyle(
            "CustomCodeBlock",
            parent=base["Normal"],
            fontSize=8.5,
            leading=12,
            fontName="Courier",
            leftIndent=0,
            rightIndent=0,
            spaceAfter=0,
            spaceBefore=0,
            backColor=colors.HexColor("#f5f5f5"),
            borderColor=colors.HexColor("#e0e0e0"),
            borderWidth=0.5,
            borderPadding=(6, 8, 6, 8),
            textColor=colors.HexColor("#333333"),
        ),
        "blockquote": ParagraphStyle(
            "CustomBlockquote",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            leftIndent=20,
            borderColor=colors.HexColor("#3498db"),
            borderWidth=2,
            borderPadding=(8, 8, 8, 12),
            backColor=colors.HexColor("#f0f7ff"),
            spaceAfter=10,
            textColor=colors.HexColor("#555555"),
            fontName="Helvetica-Oblique",
        ),
        "list_item": ParagraphStyle(
            "CustomListItem",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            leftIndent=0,
            bulletIndent=0,
            spaceAfter=4,
            textColor=colors.HexColor("#2d3436"),
        ),
        "caption": ParagraphStyle(
            "CustomCaption",
            parent=base["Normal"],
            fontSize=9,
            leading=13,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#777777"),
            spaceAfter=12,
        ),
        "info_tip": ParagraphStyle(
            "CustomInfoTip",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            leftIndent=20,
            borderColor=colors.HexColor("#2ecc71"),
            borderWidth=2,
            borderPadding=(8, 8, 8, 12),
            backColor=colors.HexColor("#eafaf1"),
            spaceAfter=10,
            textColor=colors.HexColor("#2d3436"),
        ),
        "info_note": ParagraphStyle(
            "CustomInfoNote",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            leftIndent=20,
            borderColor=colors.HexColor("#3498db"),
            borderWidth=2,
            borderPadding=(8, 8, 8, 12),
            backColor=colors.HexColor("#eaf2f8"),
            spaceAfter=10,
            textColor=colors.HexColor("#2d3436"),
        ),
        "info_warning": ParagraphStyle(
            "CustomInfoWarning",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            leftIndent=20,
            borderColor=colors.HexColor("#e67e22"),
            borderWidth=2,
            borderPadding=(8, 8, 8, 12),
            backColor=colors.HexColor("#fef5e7"),
            spaceAfter=10,
            textColor=colors.HexColor("#2d3436"),
        ),
        "dialogue_line": ParagraphStyle(
            "CustomDialogueLine",
            parent=base["Normal"],
            fontSize=10.5,
            leading=16,
            spaceAfter=6,
            textColor=colors.HexColor("#2d3436"),
        ),
        "chapter_number": ParagraphStyle(
            "CustomChapterNumber",
            parent=base["Normal"],
            fontSize=48,
            leading=56,
            spaceAfter=4,
            fontName="Helvetica-Bold",
            textColor=colors.HexColor("#16a34a"),
        ),
    }
    return styles


def _escape_xml(text: str) -> str:
    """Escape text for ReportLab XML paragraphs."""
    # Preserve markdown-style bold/italic by converting to ReportLab tags
    # First escape XML special chars (but not our markdown formatting)
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")

    # Convert markdown bold **text** to ReportLab <b>text</b>
    text = re.sub(r'\*\*(.+?)\*\*', r'<b>\1</b>', text)
    # Convert markdown italic *text* to ReportLab <i>text</i>
    text = re.sub(r'\*(.+?)\*', r'<i>\1</i>', text)
    # Convert markdown inline code `text` to <font name="Courier">text</font>
    text = re.sub(r'`(.+?)`', r'<font name="Courier" color="#c0392b">\1</font>', text)
    # Convert markdown links [text](url) — just show the text in PDF
    text = re.sub(r'\[(.+?)\]\(.+?\)', r'<u>\1</u>', text)

    return text


_CONTENT_WIDTH = A4[0] - 50 * mm  # page width minus left+right margins


def _escape_xml_plain(text: str) -> str:
    """Escape plain text (no markdown) for ReportLab XML paragraphs.

    Use this for image alt text and other AI-generated descriptions that
    contain literal characters like * and ` which are NOT markdown markers.
    """
    text = text.replace("&", "&amp;")
    text = text.replace("<", "&lt;")
    text = text.replace(">", "&gt;")
    return text


_JAVA_KEYWORDS = frozenset({
    "abstract", "assert", "boolean", "break", "byte", "case", "catch", "char",
    "class", "const", "continue", "default", "do", "double", "else", "enum",
    "extends", "final", "finally", "float", "for", "goto", "if", "implements",
    "import", "instanceof", "int", "interface", "long", "native", "new",
    "null", "package", "private", "protected", "public", "return", "short",
    "static", "strictfp", "super", "switch", "synchronized", "this", "throw",
    "throws", "transient", "true", "false", "try", "void", "volatile", "while",
    "var", "record", "sealed", "permits", "yield",
})
_PYTHON_KEYWORDS = frozenset({
    "False", "None", "True", "and", "as", "assert", "async", "await",
    "break", "class", "continue", "def", "del", "elif", "else", "except",
    "finally", "for", "from", "global", "if", "import", "in", "is",
    "lambda", "nonlocal", "not", "or", "pass", "raise", "return", "try",
    "while", "with", "yield", "self", "cls",
})
_GENERIC_KEYWORDS = _JAVA_KEYWORDS | _PYTHON_KEYWORDS

# Colour palette (matches a VS Code light theme)
_COL_KEYWORD = "#0000ff"   # blue   -- language keywords
_COL_STRING  = "#008000"   # green  -- string literals
_COL_COMMENT = "#808080"   # gray   -- line comments
_COL_NUMBER  = "#c76300"   # orange -- numeric literals
_COL_TYPE    = "#267f99"   # teal   -- PascalCase type names


def _xml_escape_raw(text: str) -> str:
    """Escape only the three XML metacharacters, leaving everything else alone."""
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _highlight_code_line(line: str, language: str) -> str:
    """Return a ReportLab-XML string for one source-code line with syntax colours.

    Processes tokens left-to-right in a single pass:
      1. Line comments (// or #)
      2. String literals (double- or single-quoted)
      3. Keywords and identifiers (PascalCase -> type colour)
      4. Numeric literals
    Everything else falls through as plain escaped text.
    """
    keywords = (
        _JAVA_KEYWORDS if language in ("java", "kotlin", "scala")
        else _PYTHON_KEYWORDS if language == "python"
        else _GENERIC_KEYWORDS
    )

    result: list[str] = []
    i = 0
    n = len(line)

    while i < n:
        ch = line[i]

        # --- line comment (// or #) ---
        if (ch == "/" and i + 1 < n and line[i + 1] == "/") or (
            ch == "#" and language in ("python", "bash", "shell", "sh", "")
        ):
            rest = _xml_escape_raw(line[i:])
            result.append(
                f'<font name="Courier-Oblique" color="{_COL_COMMENT}">{rest}</font>'
            )
            i = n
            continue

        # --- double-quoted string literal ---
        if ch == '"':
            j = i + 1
            while j < n:
                if line[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if line[j] == '"':
                    j += 1
                    break
                j += 1
            token = _xml_escape_raw(line[i:j])
            result.append(f'<font color="{_COL_STRING}">{token}</font>')
            i = j
            continue

        # --- single-quoted string literal ---
        if ch == "'":
            j = i + 1
            while j < n:
                if line[j] == "\\" and j + 1 < n:
                    j += 2
                    continue
                if line[j] == "'":
                    j += 1
                    break
                j += 1
            token = _xml_escape_raw(line[i:j])
            result.append(f'<font color="{_COL_STRING}">{token}</font>')
            i = j
            continue

        # --- word token (keyword / identifier / type) ---
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and (line[j].isalnum() or line[j] == "_"):
                j += 1
            word = line[i:j]
            escaped = _xml_escape_raw(word)
            if word in keywords:
                result.append(
                    f'<font color="{_COL_KEYWORD}"><b>{escaped}</b></font>'
                )
            elif word and word[0].isupper() and len(word) > 1:
                # PascalCase -> treat as a type / class name
                result.append(f'<font color="{_COL_TYPE}">{escaped}</font>')
            else:
                result.append(escaped)
            i = j
            continue

        # --- numeric literal ---
        if ch.isdigit() or (ch == "." and i + 1 < n and line[i + 1].isdigit()):
            j = i
            while j < n and (line[j].isalnum() or line[j] in ".xXbBoO_"):
                j += 1
            token = _xml_escape_raw(line[i:j])
            result.append(f'<font color="{_COL_NUMBER}">{token}</font>')
            i = j
            continue

        # --- anything else (operators, punctuation, spaces) ---
        result.append(_xml_escape_raw(ch))
        i += 1

    return "".join(result)


def _code_block_to_flowables(
    block_text: str,
    language: str,
    styles: dict[str, ParagraphStyle],
    content_width: float,
) -> list:
    """Render a code block as syntax-highlighted Paragraphs inside a Table box.

    Each source line becomes its own Paragraph so that ReportLab XML spans
    work correctly (ReportLab's Paragraph renderer does not support literal
    newlines inside tagged content).

    The block is wrapped in a single-cell Table to give it a gray background
    and border without inheriting the large leftIndent that Preformatted or
    the built-in Code style would impose.
    """
    lang = (language or "").lower()
    lines = block_text.rstrip().split("\n")

    line_paras: list = []
    for line in lines:
        # Expand tabs, then convert leading spaces to non-breaking spaces so
        # ReportLab does not collapse the indentation.
        expanded = line.rstrip().expandtabs(4)
        leading_spaces = len(expanded) - len(expanded.lstrip())
        indent_str = "\u00a0" * leading_spaces
        code_content = expanded[leading_spaces:]

        highlighted = _highlight_code_line(code_content, lang)
        xml_line = indent_str + highlighted if highlighted else "\u00a0"
        line_paras.append(Paragraph(xml_line, styles["code"]))

    # One row per line so ReportLab can split the table across page breaks
    # (a single-cell table taller than the page frame causes "Flowable too large").
    n = len(line_paras)
    table_data = [[p] for p in line_paras]
    tbl = Table(table_data, colWidths=[content_width - 2], splitByRow=1)
    style_cmds = [
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5f5f5")),
        # Outer border: draw each side explicitly so it survives page splits.
        ("LINEBEFORE",  (0, 0), (0, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("LINEAFTER",   (0, 0), (0, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("LINEABOVE",   (0, 0), (0,  0), 0.5, colors.HexColor("#e0e0e0")),
        ("LINEBELOW",   (0, -1), (0, -1), 0.5, colors.HexColor("#e0e0e0")),
        ("LEFTPADDING",  (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        # Top padding only on first row, bottom padding only on last row.
        ("TOPPADDING",    (0, 0),    (0, 0),    8),
        ("TOPPADDING",    (0, 1),    (0, -1),   0),
        ("BOTTOMPADDING", (0, 0),    (0, n - 2), 0),
        ("BOTTOMPADDING", (0, n - 1), (0, n - 1), 8),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]
    tbl.setStyle(TableStyle(style_cmds))
    return [Spacer(1, 6), tbl, Spacer(1, 6)]


_WEB_CONTENT_WIDTH_PX = 940.0
"""Approximate CSS pixel width of the ByteByteGo article content column.

Used to convert native image pixel dimensions into a proportional PDF width.
A 624px-wide image on a 700px column occupies 89% of the column; we apply that
same proportion to the PDF content width so large and small images scale
naturally rather than being forced to the same fixed cap.
"""


def _proportional_pdf_width(native_px: float) -> float:
    """Return the target PDF width (in points) for an image that is native_px
    wide in the original web layout.

    The result is capped at 95% of the usable content width so images never
    overflow the page, but small images (e.g. half-column diagrams) are kept
    proportionally smaller rather than all being stretched to a uniform cap.
    """
    fraction = native_px / _WEB_CONTENT_WIDTH_PX
    target = _CONTENT_WIDTH * fraction
    return min(target, _CONTENT_WIDTH * 0.95)


def _fetch_image(url: str):
    """Download an image and return a ReportLab flowable.

    Handles both raster images (PNG/JPG) and SVG images.
    Returns an Image or Drawing flowable, or None on failure.

    Image width is scaled proportionally: the native pixel width is compared
    to the website's content column width (~700 px) to derive a fraction, and
    that fraction is applied to the PDF content width.  This means a 624 px
    image (89% of column) appears near full-width while a 326 px image (47%)
    appears at roughly half width — matching the original web proportions.
    The PDF width is capped at 95% of the content area and the height is capped
    at half the page height; images are never upscaled beyond their native size.
    """
    # Hard cap on height to prevent a single image from dominating a page.
    max_height = min(A4[1] - 80 * mm, A4[1] * 0.50)

    try:
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36"
            )
        }
        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()

        content_type = resp.headers.get("content-type", "")

        if "svg" in content_type or url.endswith(".svg"):
            # Convert SVG to a ReportLab Drawing using svglib
            from svglib.svglib import svg2rlg

            svg_data = io.BytesIO(resp.content)
            drawing = svg2rlg(svg_data)
            if drawing is None:
                return None

            orig_w = drawing.width
            orig_h = drawing.height
            if orig_w > 0 and orig_h > 0:
                target_w = _proportional_pdf_width(orig_w)
                # Never upscale; also never exceed the height cap.
                scale = min(target_w / orig_w, max_height / orig_h, 1.0)
                drawing.width = orig_w * scale
                drawing.height = orig_h * scale
                drawing.scale(scale, scale)

            drawing.hAlign = "CENTER"
            return drawing

        # Raster image
        img_data = io.BytesIO(resp.content)
        img = Image(img_data)

        orig_w, orig_h = img.imageWidth, img.imageHeight
        if orig_w > 0 and orig_h > 0:
            target_w = _proportional_pdf_width(orig_w)
            # Never upscale; also never exceed the height cap.
            scale = min(target_w / orig_w, max_height / orig_h, 1.0)
            img.drawWidth = orig_w * scale
            img.drawHeight = orig_h * scale

        img.hAlign = "CENTER"
        return img
    except Exception as exc:
        print(f"  Warning: could not load image {url}: {exc}")
        return None


def _add_footer(canvas, doc):
    """Add page number footer."""
    canvas.saveState()
    canvas.setFont("Helvetica", 8)
    canvas.setFillColor(colors.HexColor("#999999"))
    page_num = canvas.getPageNumber()
    canvas.drawCentredString(A4[0] / 2, 15 * mm, f"— {page_num} —")
    canvas.restoreState()


def content_to_flowables(
    page: PageContent, styles: dict[str, ParagraphStyle]
) -> list:
    """Convert PageContent blocks into ReportLab flowables."""
    story = []

    # If the first block is a chapter-number, render it before the title so the
    # large green number appears at the very top (matching the ByteByteGo page layout).
    blocks = page.blocks
    if blocks and blocks[0].tag == "chapter-number":
        story.append(Paragraph(blocks[0].text, styles["chapter_number"]))
        story.append(Spacer(1, 4))
        blocks = blocks[1:]

    # Add page title
    story.append(Paragraph(_escape_xml(page.title), styles["title"]))
    story.append(Spacer(1, 12))

    for block in blocks:
        if block.tag == "heading":
            level = block.level
            style_key = f"h{min(level, 3)}"
            # Use title style for the first h1/h2
            if level <= 1:
                style_key = "title"
            text = _escape_xml(block.text)
            story.append(Paragraph(text, styles[style_key]))

        elif block.tag == "p":
            raw = block.text
            # Math blocks ($$...$$ display math): strip delimiters, render as monospace
            if raw.startswith("$$") and raw.endswith("$$"):
                inner = raw[2:-2].strip()
                text = _escape_xml(inner)
                story.append(Paragraph(text, styles["code"]))
            else:
                text = _escape_xml(raw)
                story.append(Paragraph(text, styles["body"]))

        elif block.tag == "img":
            img = _fetch_image(block.src)
            if img is not None:
                story.append(Spacer(1, 6))
                story.append(img)
                if block.alt:
                    story.append(
                        Paragraph(_escape_xml_plain(block.alt), styles["caption"])
                    )
                story.append(Spacer(1, 6))

        elif block.tag in ("ul", "ol"):
            for i, item in enumerate(block.children):
                bullet = f"{i + 1}." if block.tag == "ol" else "\u2022"
                text = _escape_xml(item)
                story.append(
                    Paragraph(
                        f"{bullet}  {text}",
                        styles["list_item"],
                    )
                )
            story.append(Spacer(1, 6))

        elif block.tag == "pre":
            # Render with syntax highlighting and a Table-based background box.
            story.extend(
                _code_block_to_flowables(
                    block.text, block.language, styles, _CONTENT_WIDTH
                )
            )

        elif block.tag == "blockquote":
            text = _escape_xml(block.text)
            story.append(Paragraph(text, styles["blockquote"]))

        elif block.tag == "table":
            if block.headers or block.children:
                # Build a proper ReportLab Table from structured data
                table_data = []
                if block.headers:
                    table_data.append([
                        Paragraph(f"<b>{_escape_xml(h)}</b>", styles["body"])
                        for h in block.headers
                    ])
                for row in block.children:
                    table_data.append([
                        Paragraph(_escape_xml(cell), styles["body"])
                        for cell in row
                    ])
                if table_data:
                    num_cols = max(len(r) for r in table_data)
                    col_w = _CONTENT_WIDTH / num_cols
                    tbl = Table(table_data, colWidths=[col_w] * num_cols)
                    tbl_style = [
                        ("GRID", (0, 0), (-1, -1), 0.5, colors.HexColor("#cccccc")),
                        ("VALIGN", (0, 0), (-1, -1), "TOP"),
                        ("LEFTPADDING", (0, 0), (-1, -1), 6),
                        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
                        ("TOPPADDING", (0, 0), (-1, -1), 4),
                        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                    ]
                    if block.headers:
                        tbl_style.append(
                            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f0f0f0"))
                        )
                    tbl.setStyle(TableStyle(tbl_style))
                    story.append(Spacer(1, 6))
                    story.append(tbl)
                    story.append(Spacer(1, 6))
            elif block.text:
                text = _escape_xml(block.text.replace("\n", " "))
                story.append(Paragraph(text, styles["body"]))

        elif block.tag == "hr":
            story.append(Spacer(1, 6))
            story.append(
                HRFlowable(
                    width="80%",
                    color=colors.HexColor("#cccccc"),
                    thickness=0.5,
                )
            )
            story.append(Spacer(1, 6))

        elif block.tag == "info-box":
            admon = block.admonition_type.lower()
            style_key = f"info_{admon}" if f"info_{admon}" in styles else "info_note"
            label = block.admonition_type or "Note"
            text = _escape_xml(block.text)
            story.append(Spacer(1, 4))
            story.append(
                Paragraph(f"<b>{label}:</b> {text}", styles[style_key])
            )
            story.append(Spacer(1, 4))

        elif block.tag == "sample-dialogue":
            # Wrap all dialogue lines in a single bordered box
            inner = []
            for line in block.children:
                text = _escape_xml(line)
                inner.append(Paragraph(text, styles["dialogue_line"]))
            if inner:
                # Remove spaceAfter from the last paragraph
                story.append(Spacer(1, 6))
                tbl = Table(
                    [[inner]],
                    colWidths=[_CONTENT_WIDTH],
                )
                tbl.setStyle(TableStyle([
                    ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#f5eef8")),
                    ("BOX", (0, 0), (-1, -1), 2, colors.HexColor("#9b59b6")),
                    ("LEFTPADDING", (0, 0), (-1, -1), 12),
                    ("RIGHTPADDING", (0, 0), (-1, -1), 10),
                    ("TOPPADDING", (0, 0), (-1, -1), 10),
                    ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
                ]))
                story.append(tbl)
                story.append(Spacer(1, 8))

        elif block.tag == "chapter-number":
            # Render as large green bold number (fallback if not at position 0)
            story.append(Paragraph(block.text, styles["chapter_number"]))
            story.append(Spacer(1, 4))

    return story


def generate_pdf_filename(page: PageContent) -> str:
    """Generate the PDF filename from chapter number and title.

    Handles both int chapter numbers (OOD course: 1 → "01") and
    string chapter numbers (Coding Patterns course: "01-00" → "01-00").
    """
    num = str(page.chapter_number) if isinstance(page.chapter_number, str) else f"{page.chapter_number:02d}"
    title = re.sub(r'[<>:"/\\|?*]', '', page.title)
    title = re.sub(r'\s+', ' ', title).strip()
    return f"{num}. {title}.pdf"


def save_pdf(page: PageContent, output_dir: str | Path) -> Path:
    """Convert PageContent to a styled PDF and save.

    Returns the path to the saved file.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    filename = generate_pdf_filename(page)
    filepath = output_dir / filename

    doc = SimpleDocTemplate(
        str(filepath),
        pagesize=A4,
        leftMargin=25 * mm,
        rightMargin=25 * mm,
        topMargin=25 * mm,
        bottomMargin=25 * mm,
        title=page.title,
    )

    styles = _build_styles()
    story = content_to_flowables(page, styles)

    doc.build(story, onFirstPage=_add_footer, onLaterPages=_add_footer)
    return filepath
