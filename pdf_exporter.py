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
    Image,
    PageTemplate,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
    Preformatted,
    HRFlowable,
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
            parent=base["Code"],
            fontSize=9,
            leading=13,
            fontName="Courier",
            backColor=colors.HexColor("#f5f5f5"),
            borderColor=colors.HexColor("#e0e0e0"),
            borderWidth=0.5,
            borderPadding=6,
            spaceAfter=10,
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
            leftIndent=24,
            bulletIndent=12,
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


def _fetch_image(url: str, max_width: float = 450):
    """Download an image and return a ReportLab flowable.

    Handles both raster images (PNG/JPG) and SVG images.
    Returns an Image or Drawing flowable, or None on failure.
    """
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

            # Scale to fit max_width AND max page height
            max_height = A4[1] - 80 * mm  # leave room for margins + footer
            orig_w = drawing.width
            orig_h = drawing.height
            if orig_w > 0 and orig_h > 0:
                scale = min(max_width / orig_w, max_height / orig_h, 1.0)
                drawing.width = orig_w * scale
                drawing.height = orig_h * scale
                drawing.scale(scale, scale)

            return drawing

        # Raster image
        img_data = io.BytesIO(resp.content)
        img = Image(img_data)

        orig_w, orig_h = img.imageWidth, img.imageHeight
        if orig_w > 0:
            scale = min(max_width / orig_w, 1.0)
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


_CONTENT_WIDTH = A4[0] - 50 * mm  # page width minus left+right margins


def content_to_flowables(
    page: PageContent, styles: dict[str, ParagraphStyle]
) -> list:
    """Convert PageContent blocks into ReportLab flowables."""
    story = []

    # Add page title
    story.append(Paragraph(_escape_xml(page.title), styles["title"]))
    story.append(Spacer(1, 12))

    for block in page.blocks:
        if block.tag == "heading":
            level = block.level
            style_key = f"h{min(level, 3)}"
            # Use title style for the first h1/h2
            if level <= 1:
                style_key = "title"
            text = _escape_xml(block.text)
            story.append(Paragraph(text, styles[style_key]))

        elif block.tag == "p":
            text = _escape_xml(block.text)
            story.append(Paragraph(text, styles["body"]))

        elif block.tag == "img":
            img = _fetch_image(block.src)
            if img is not None:
                story.append(Spacer(1, 6))
                story.append(img)
                if block.alt:
                    story.append(
                        Paragraph(_escape_xml(block.alt), styles["caption"])
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
            # Use Preformatted for code blocks to preserve whitespace
            code_text = block.text.rstrip()
            story.append(Spacer(1, 4))
            story.append(
                Preformatted(code_text, styles["code"])
            )
            story.append(Spacer(1, 4))

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

    return story


def generate_pdf_filename(page: PageContent) -> str:
    """Generate the PDF filename from chapter number and title."""
    num = f"{page.chapter_number:02d}"
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
