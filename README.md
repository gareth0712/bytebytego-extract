# ByteByteGo Course Extractor

Extract content from [ByteByteGo](https://bytebytego.com) course pages and export as Markdown and PDF.

## Quick Start

```bash
pip install -r requirements.txt
python __init__.py "https://bytebytego.com/courses/object-oriented-design-interview/what-is-an-object-oriented-design-interview" -o ./output
```

This produces:
- `01. What is an Object-Oriented Design Interview.md`
- `01. What is an Object-Oriented Design Interview.pdf`

## Authentication (for Paid Chapters)

Free chapters work without login. For paid chapters, export your browser cookies to a Netscape-format file at `S:\program-files\cookies\cookies-bytebytego.txt`. The tool reads the `token` cookie automatically.

To export cookies: log in to bytebytego.com in Chrome, use the "Get cookies.txt LOCALLY" extension, and save the file to the path above.

## How It Works

### The Problem

ByteByteGo is built with Next.js and uses MDX (Markdown + JSX) for course content. When you view a chapter page, the HTML body is essentially **empty** — there's no readable content in the DOM. Instead, the actual text, headings, images, code snippets, and tables are embedded inside a `<script>` tag as a JSON blob called `__NEXT_DATA__`.

### Step 1: Extract the JSON Data

We fetch the page HTML and pull out the `__NEXT_DATA__` JSON. Inside it, `props.pageProps.code` contains the chapter content as a **compiled JavaScript module** — the MDX source has been transformed into a JS function that returns React JSX calls.

### Step 2: Parse the Compiled MDX

The JS module looks like this (simplified):
```javascript
(0,e.jsx)(C.p,{children:"This is a paragraph."})
(0,e.jsx)(C.h2,{id:"section-title",children:"Section Title"})
(0,e.jsx)(C.pre,{children:(0,e.jsx)(C.code,{className:"hljs language-java",children:[...]})})
```

Each line is a JSX function call representing a content element — paragraphs, headings, images, lists, code blocks, tables, etc. We use regex patterns to identify each element type and extract its text content.

Key challenges solved:
- **Variable names change between chapters** — The minified JS uses different single-letter variables (`e`, `C`, `i`) in different chapters. We match any variable name.
- **Nested content** — Code blocks contain arrays of syntax-highlighted `<span>` elements mixed with string literals. We walk the array and concatenate the plain text.
- **Element splitting** — Top-level elements are separated by backtick-newline patterns, but the same pattern appears inside nested structures (lists, code blocks). We track parenthesis/bracket depth to only split at the top level.

### Step 3: Convert to Markdown

Each parsed element maps to Markdown syntax:
- Headings become `##`, `###`, etc.
- Info boxes become GitHub admonition blocks (`> [!TIP]`, `> [!NOTE]`)
- Dialogue sections become speaker-labeled blockquotes
- Code blocks become fenced code with language tags
- Tables become pipe-delimited Markdown tables
- Images link to the original ByteByteGo CDN URLs

### Step 4: Export to PDF

Using ReportLab, we generate a styled PDF with:
- Custom typography and heading hierarchy
- SVG images rendered via svglib (ByteByteGo uses SVG exclusively)
- Color-coded callout boxes for tips, notes, and dialogue
- Proper table rendering with header row styling
- Code blocks in monospace font with grey background
- Page numbers in the footer

## Project Structure

```
__init__.py            # CLI entry point
fetcher.py             # Fetches pages, parses compiled MDX into ContentBlock objects
markdown_converter.py  # Converts ContentBlock list to .md files
pdf_exporter.py        # Converts ContentBlock list to styled .pdf files
requirements.txt       # Python dependencies
CLAUDE.md              # Agent context for AI-assisted development
Reference.md           # Detailed MDX internals and raw JSX samples
```

## Supported Content Types

| Element | Markdown | PDF |
|---------|----------|-----|
| Headings (h1-h6) | `#` to `######` | Styled hierarchy |
| Paragraphs | Plain text with **bold**, *italic*, `code` | Helvetica body text |
| Images (SVG) | `![alt](url)` | Embedded SVG drawings |
| Unordered/ordered lists | `- item` / `1. item` | Bulleted paragraphs |
| Code blocks (Java, etc.) | Fenced ` ```java ``` ` | Courier font, grey box |
| Tables | Pipe-delimited | ReportLab Table with grid |
| Info boxes (Tip/Note) | `> [!TIP]` admonition | Colored border + background |
| Dialogue sections | `> **Speaker:** text` | Purple bordered box |
| Blockquotes | `> text` | Italic, blue border |

## Dependencies

- `requests` — HTTP fetching
- `beautifulsoup4` — Legacy HTML table fallback
- `reportlab` — PDF generation
- `svglib` — SVG to ReportLab drawing conversion
