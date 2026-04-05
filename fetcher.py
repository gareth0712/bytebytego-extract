"""Fetch and parse content from ByteByteGo course pages.

ByteByteGo uses Next.js with compiled MDX. The actual content lives in
a __NEXT_DATA__ JSON blob as a bundled JS module, not in rendered HTML.
This module extracts structured content by parsing that JS bundle.
"""

import json
import re
from dataclasses import dataclass, field
from urllib.parse import urljoin

import requests

BASE_URL = "https://bytebytego.com"
COOKIE_PATH = "S:/program-files/cookies/cookies-bytebytego.txt"

# In compiled MDX, BOTH the jsx caller AND the component namespace are
# single-letter variables that change between articles:
#   Ch1: (0,e.jsx)(C.p,...) — e=caller, C=namespace
#   Ch3: (0,C.jsx)(e.p,...) — C=caller, e=namespace
# We use \w+ for both positions.
_J = r"\w+"   # jsx caller variable  (was hardcoded as `e`)
_V = r"\w+"   # component namespace  (was hardcoded as `C` or `i`)


@dataclass
class ContentBlock:
    """Represents a parsed content element from the page."""
    tag: str  # "heading", "p", "img", "ul", "ol", "pre", "blockquote",
              # "hr", "info-box", "sample-dialogue", "table"
    text: str = ""
    level: int = 0  # heading level for h1-h6
    src: str = ""  # image source URL
    alt: str = ""  # image alt text
    children: list = field(default_factory=list)  # lists/dialogue/table rows
    language: str = ""  # for code blocks
    admonition_type: str = ""  # "Tip", "Note", "Warning" for info-box
    headers: list = field(default_factory=list)  # table header cells


@dataclass
class PageContent:
    """Parsed page content ready for conversion."""
    title: str
    chapter_number: int
    blocks: list[ContentBlock]


def _load_cookies() -> dict[str, str]:
    """Load auth cookies from the Netscape cookie file if it exists."""
    try:
        cookies = {}
        with open(COOKIE_PATH, "r") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                parts = line.split("\t")
                if len(parts) >= 7:
                    cookies[parts[5]] = parts[6]
        return cookies
    except FileNotFoundError:
        return {}


def fetch_page(url: str, cookies: dict[str, str] | None = None) -> str:
    """Fetch raw HTML from a URL, optionally with auth cookies."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/146.0.0.0 Safari/537.36"
        )
    }
    if cookies is None:
        cookies = _load_cookies()
    response = requests.get(url, headers=headers, cookies=cookies, timeout=30)
    response.raise_for_status()
    return response.text


def _extract_next_data(html: str) -> dict:
    """Extract the __NEXT_DATA__ JSON from the HTML page."""
    match = re.search(
        r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if not match:
        raise ValueError("Could not find __NEXT_DATA__ in page HTML")
    return json.loads(match.group(1))


def _resolve_image_url(src: str) -> str:
    """Convert relative image paths to absolute URLs."""
    if src.startswith("http"):
        return src
    return urljoin(BASE_URL, src)


def _unescape(text: str) -> str:
    """Decode \\uXXXX and other JS escape sequences."""
    return text.encode().decode("unicode_escape", errors="replace")


# ---------------------------------------------------------------------------
# Inline children parser
# ---------------------------------------------------------------------------

def _parse_jsxs_children(inner: str) -> str:
    """Parse children array of a jsxs call into combined text.

    Handles inline elements like strong, em, code, and links mixed
    with plain string literals and backtick template literals.
    """
    jsx_parts: list[tuple[int, int, str]] = []  # (start, end, rendered_text)

    # Strong
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.strong,\{{children:"((?:[^"\\]|\\.)*)"\}}\)', inner
    ):
        jsx_parts.append((m.start(), m.end(), f"**{_unescape(m.group(1))}**"))

    # Em
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.em,\{{children:"((?:[^"\\]|\\.)*)"\}}\)', inner
    ):
        jsx_parts.append((m.start(), m.end(), f"*{_unescape(m.group(1))}*"))

    # Inline code
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.code,\{{children:"((?:[^"\\]|\\.)*)"\}}\)', inner
    ):
        jsx_parts.append((m.start(), m.end(), f"`{_unescape(m.group(1))}`"))

    # Links
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.a,\{{href:"([^"]*)",children:"((?:[^"\\]|\\.)*)"\}}\)',
        inner,
    ):
        href = m.group(1)
        text = _unescape(m.group(2))
        jsx_parts.append((m.start(), m.end(), f"[{text}]({href})"))

    # Collect all literal ranges (jsx + standalone) to prevent overlapping matches.
    # Single-quoted strings can contain double quotes and vice versa, so we
    # process from longest-span first and skip any literal whose position
    # falls inside an already-claimed range.
    claimed_ranges: list[tuple[int, int]] = []
    for s, e, _ in jsx_parts:
        claimed_ranges.append((s, e))

    plain_parts: list[tuple[int, str]] = []

    # Backtick template literals first (they can contain both 'single' and
    # "double" quoted words as prose — claim them before shorter quote matches
    # extract those words as duplicates).
    for m in re.finditer(r'`((?:[^`\\]|\\.)*)`', inner):
        pos, end = m.start(), m.end()
        if any(s <= pos < e for s, e in claimed_ranges):
            continue
        text = m.group(1)
        text = text.encode().decode("unicode_escape", errors="replace")
        text = re.sub(r'\s+', ' ', text)
        plain_parts.append((pos, text))
        claimed_ranges.append((pos, end))

    # Single-quoted strings (they can contain "double quotes" inside)
    for m in re.finditer(r"'((?:[^'\\]|\\.)*)'", inner):
        pos, end = m.start(), m.end()
        if any(s <= pos < e for s, e in claimed_ranges):
            continue
        prefix = inner[max(0, pos - 15):pos]
        if re.search(r'(?:className|id|src|alt|href|width|height):$', prefix):
            continue
        plain_parts.append((pos, m.group(1)))
        claimed_ranges.append((pos, end))

    # Double-quoted strings
    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', inner):
        pos, end = m.start(), m.end()
        if any(s <= pos < e for s, e in claimed_ranges):
            continue
        prefix = inner[max(0, pos - 15):pos]
        if re.search(r'(?:className|id|src|alt|href|width|height):$', prefix):
            continue
        plain_parts.append((pos, _unescape(m.group(1))))
        claimed_ranges.append((pos, end))

    # Merge all parts sorted by position
    all_parts: list[tuple[int, str]] = []
    for start, _end, text in jsx_parts:
        all_parts.append((start, text))
    all_parts.extend(plain_parts)
    all_parts.sort(key=lambda x: x[0])

    return "".join(text for _, text in all_parts)


# ---------------------------------------------------------------------------
# Element splitter
# ---------------------------------------------------------------------------

def _split_top_level_elements(content_js: str) -> list[str]:
    """Split the Fragment children array into individual top-level JSX calls.

    Elements are separated by ,`\\n` in the compiled MDX, but we must
    only split at the top level (depth 0), not inside nested structures
    like lists or code block children arrays.
    """
    elements = []
    current: list[str] = []
    depth = 0        # parenthesis nesting
    bracket_depth = 0  # square bracket nesting
    i = 0
    n = len(content_js)

    while i < n:
        ch = content_js[i]

        if ch == '(':
            depth += 1
            current.append(ch)
        elif ch == ')':
            depth -= 1
            current.append(ch)
        elif ch == '[':
            bracket_depth += 1
            current.append(ch)
        elif ch == ']':
            bracket_depth -= 1
            current.append(ch)
        elif ch == '`':
            if depth == 0 and bracket_depth == 0:
                # Top-level backtick separator — skip the ,`\n` gap
                j = i + 1
                while j < n and content_js[j] != '`':
                    j += 1
                i = j + 1
                # Skip trailing comma/whitespace
                while i < n and content_js[i] in (',', ' ', '\n'):
                    i += 1
                # Save current element and start fresh
                text = "".join(current).strip().rstrip(',')
                if text:
                    elements.append(text)
                current = []
                continue
            elif bracket_depth > 0:
                # Nested backtick template literal (inside code block
                # children array, etc.) — consume the whole literal
                current.append(ch)
                i += 1
                while i < n and content_js[i] != '`':
                    current.append(content_js[i])
                    i += 1
                if i < n:
                    current.append(content_js[i])  # closing backtick
            else:
                # depth > 0 but bracket_depth == 0: we are inside a JSX call
                # but not inside a [...] array. The backtick is part of the
                # current element (e.g. a template-literal prop value) and
                # must NOT trigger the consumption loop, which could eat past
                # the element boundary.
                current.append(ch)
        elif ch == '"':
            # Skip string literals to avoid false depth changes
            current.append(ch)
            i += 1
            while i < n and content_js[i] != '"':
                if content_js[i] == '\\':
                    current.append(content_js[i])
                    i += 1
                    if i < n:
                        current.append(content_js[i])
                        i += 1
                    continue
                current.append(content_js[i])
                i += 1
            if i < n:
                current.append(content_js[i])  # closing quote
        else:
            current.append(ch)

        i += 1

    text = "".join(current).strip().rstrip(',')
    if text:
        elements.append(text)

    return elements


# ---------------------------------------------------------------------------
# Syntax-highlighted code block extractor
# ---------------------------------------------------------------------------

def _extract_hljs_code_text(inner: str) -> str:
    """Extract plain text from an hljs syntax-highlighted children array.

    Children is a mix of:
      - (0,J.jsx)(V.span,{className:"hljs-keyword",children:"public"})
      - (0,J.jsxs)(V.span,{className:"hljs-params",children:[...]})  (nested)
      - plain strings: " " or "(" (JSX array separators / code fragments)
      - backtick templates: ` {\\n    ` (code with newlines)

    IMPORTANT: Unlike prose parsing, standalone "..." strings in code blocks
    are actual code (e.g., " + color + " or "("). The quote characters are
    meaningful Java/code syntax and must NOT be stripped.
    """
    # Step 1: find precise JSX span ranges and extract their children text
    span_entries: list[tuple[int, int, str]] = []  # (start, end, text)

    for m in re.finditer(rf'\(0,{_J}\.jsx[s]?\)\({_V}\.span,', inner):
        start = m.start()
        # The JSX call has the form: (0,J.jsx)(V.span,{...})
        # Two paren groups. We need the end of the SECOND group.
        # Skip past the first group "(0,J.jsx)" then track the second.
        first_close = inner.find(')', start) + 1  # end of (0,J.jsx)
        d = 0
        end = first_close
        for idx in range(first_close, min(first_close + 5000, len(inner))):
            if inner[idx] == '(':
                d += 1
            elif inner[idx] == ')':
                d -= 1
                if d == 0:
                    end = idx + 1
                    break
        span_body = inner[start:end]

        # Extract text from children: only. Use lookbehind to skip className.
        texts: list[tuple[int, str]] = []

        # First, find all children:[...] array ranges so we can skip direct
        # pattern matches that fall inside them (recursion handles those).
        array_ranges: list[tuple[int, int]] = []
        for ar in re.finditer(r'children:\[', span_body):
            ar_start = ar.start()
            bd2 = 1
            ar_end = ar.end()
            for idx2 in range(ar.end(), len(span_body)):
                if span_body[idx2] == '[':
                    bd2 += 1
                elif span_body[idx2] == ']':
                    bd2 -= 1
                    if bd2 == 0:
                        ar_end = idx2 + 1
                        break
            array_ranges.append((ar_start, ar_end))

        # children:"double-quoted string"
        for sm in re.finditer(r'(?<=children:)"((?:[^"\\]|\\.)*)"', span_body):
            if any(s <= sm.start() < e for s, e in array_ranges):
                continue
            texts.append((sm.start(), _unescape(sm.group(1))))

        # children:'single-quoted string' (used when text contains double quotes,
        # e.g. '"This shape is "' where the " are Java string delimiters)
        for sm in re.finditer(r"(?<=children:)'((?:[^'\\]|\\.)*)'", span_body):
            if any(s <= sm.start() < e for s, e in array_ranges):
                continue
            texts.append((sm.start(), sm.group(1)))

        # children:`backtick template` (used when text has both quote types,
        # e.g. `"Robots don't eat."`)
        for sm in re.finditer(r'(?<=children:)`((?:[^`\\]|\\.)*)`', span_body):
            if any(s <= sm.start() < e for s, e in array_ranges):
                continue
            t = sm.group(1).encode().decode("unicode_escape", errors="replace")
            texts.append((sm.start(), t))

        # Compound children:[...] — recurse into the array
        for sm in re.finditer(r'children:\[', span_body):
            arr_start = sm.end()
            bd = 1
            arr_end = arr_start
            for idx in range(arr_start, len(span_body)):
                if span_body[idx] == '[':
                    bd += 1
                elif span_body[idx] == ']':
                    bd -= 1
                    if bd == 0:
                        arr_end = idx
                        break
            arr_content = span_body[arr_start:arr_end]
            # Recursively extract from the sub-array
            sub_text = _extract_hljs_code_text(arr_content)
            if sub_text:
                texts.append((arr_start, sub_text))

        texts.sort(key=lambda x: x[0])
        span_text = "".join(t for _, t in texts)
        span_entries.append((start, end, span_text))

    # Filter out nested spans (inner spans already handled by recursion)
    filtered_entries = []
    for i, (s1, e1, t1) in enumerate(span_entries):
        is_nested = False
        for j, (s2, e2, _) in enumerate(span_entries):
            if i != j and s2 <= s1 and e1 <= e2:
                is_nested = True
                break
        if not is_nested:
            filtered_entries.append((s1, e1, t1))
    span_entries = filtered_entries

    # Step 2: collect standalone literals NOT inside any span.
    standalone: list[tuple[int, str]] = []

    for m in re.finditer(r'"((?:[^"\\]|\\.)*)"', inner):
        pos = m.start()
        if any(s <= pos < e for s, e, _ in span_entries):
            continue
        # Skip JSX property values (className, id, etc.)
        prefix = inner[max(0, pos - 12):pos]
        if re.search(r'(?:className|id|src|alt|href|width|height):$', prefix):
            continue
        # In code blocks, standalone strings ARE code. Keep the content as-is.
        standalone.append((pos, _unescape(m.group(1))))

    for m in re.finditer(r'`((?:[^`\\]|\\.)*)`', inner):
        pos = m.start()
        if any(s <= pos < e for s, e, _ in span_entries):
            continue
        text = m.group(1).encode().decode("unicode_escape", errors="replace")
        standalone.append((pos, text))

    # Step 3: merge and sort by position
    all_parts: list[tuple[int, str]] = []
    for start, _end, text in span_entries:
        all_parts.append((start, text))
    all_parts.extend(standalone)
    all_parts.sort(key=lambda x: x[0])

    result = "".join(t for _, t in all_parts)

    # Post-process: fix hljs highlighter bugs in source data.
    # ByteByteGo's hljs sometimes:
    # 1. Merges a // comment and the following code line into one span
    # 2. Misclassifies code lines as hljs-comment spans (adding "// " prefix)
    _CODE_KEYWORDS = re.compile(
        r'(?:private|public|protected|final|static|class|interface|enum|abstract|'
        r'import|return|this|int|void|String|boolean|long|double|float|byte|short|'
        r'char|List|Map|Set|@Override|@)\b'
    )
    lines = result.split('\n')
    fixed_lines: list[str] = []
    for i, line in enumerate(lines):
        stripped = line.lstrip()
        indent = line[:len(line) - len(stripped)]

        # 1. Split merged comment+code lines:
        #    "    // comment text     private final X;" →
        #    "    // comment text" + "\n" + "    private final X;"
        if stripped.startswith('//'):
            m = re.search(r'(//.*?)\s{4,}(' + _CODE_KEYWORDS.pattern + r')', stripped)
            if m:
                fixed_lines.append(indent + m.group(1).rstrip())
                remainder = stripped[m.start(2):]
                fixed_lines.append(indent + remainder)
                continue

        # 2. Fix misclassified code-as-comment:
        #    If previous line ends with "=" or "= new" and current line is
        #    "// value;" — strip the "// " prefix and join with previous line.
        if stripped.startswith('// ') and fixed_lines:
            prev = fixed_lines[-1].rstrip()
            after_slashes = stripped[3:]  # text after "// "
            if (prev.endswith('=') or prev.endswith('= new') or
                    prev.endswith('= new ')) and ';' in after_slashes:
                fixed_lines[-1] = prev + ' ' + after_slashes
                continue

        # 3. Fix misclassified code continuation:
        #    If previous line ends with a type name (capitalized) and current
        #    line is "// identifier;" — strip "// " and join.
        if stripped.startswith('// ') and fixed_lines:
            prev = fixed_lines[-1].rstrip()
            after_slashes = stripped[3:]
            if (re.search(r'[A-Z]\w+$', prev) and
                    re.match(r'\w+;$', after_slashes)):
                fixed_lines[-1] = prev + ' ' + after_slashes
                continue

        fixed_lines.append(line)

    return '\n'.join(fixed_lines)


# ---------------------------------------------------------------------------
# Table parser
# ---------------------------------------------------------------------------

def _parse_table_cell(cell_content: str) -> str:
    """Extract text from a table cell (th or td) JSX content."""
    # Simple string: children:"text"
    m = re.search(r'children:"((?:[^"\\]|\\.)*)"', cell_content)
    if m:
        return _unescape(m.group(1))
    # Compound children: children:[...]
    m = re.search(r'children:\[(.*?)\]', cell_content, re.DOTALL)
    if m:
        return _parse_jsxs_children(m.group(1))
    return ""


def _parse_table_element(element: str) -> ContentBlock | None:
    """Parse a table-wrap div element into a ContentBlock."""
    headers: list[str] = []
    rows: list[list[str]] = []

    # Extract header cells from thead > tr > th
    thead_match = re.search(
        rf'{_V}\.thead,\{{children:.*?{_V}\.tr,\{{children:\[(.*?)\]\}}',
        element, re.DOTALL,
    )
    if thead_match:
        for m in re.finditer(rf'{_V}\.th,\{{(.*?)\}}', thead_match.group(1)):
            headers.append(_parse_table_cell(m.group(1)))

    # Extract body rows from tbody > tr > td
    tbody_match = re.search(
        rf'{_V}\.tbody,\{{children:\[(.*)\]\}}',
        element, re.DOTALL,
    )
    if tbody_match:
        # Each row: (0,J.jsxs)(V.tr,{children:[...td...]})
        for row_match in re.finditer(
            rf'\(0,{_J}\.jsxs?\)\({_V}\.tr,\{{children:\[(.*?)\]\}}\)',
            tbody_match.group(1), re.DOTALL,
        ):
            cells = []
            for td_match in re.finditer(
                rf'\(0,{_J}\.jsx[s]?\)\({_V}\.td,\{{(.*?)\}}\)',
                row_match.group(1), re.DOTALL,
            ):
                cells.append(_parse_table_cell(td_match.group(1)))
            if cells:
                rows.append(cells)

    if headers or rows:
        return ContentBlock(tag="table", headers=headers, children=rows)
    return None


# ---------------------------------------------------------------------------
# Single element parser
# ---------------------------------------------------------------------------

def _parse_single_element(element: str, img_vars: dict[str, str]) -> ContentBlock | None:
    """Parse a single top-level JSX element into a ContentBlock."""

    # Heading with no children (e.g. empty h3): (0,J.jsx)(V.h3,{id:""}) — skip silently
    m = re.match(
        rf'\(0,{_J}\.jsx[s]?\)\({_V}\.(h[1-6]),\{{id:"[^"]*"\}}\)',
        element,
    )
    if m:
        return None

    # Heading (simple string): (0,J.jsx)(V.h2,{id:"...",children:"..."})
    m = re.match(
        rf'\(0,{_J}\.jsx[s]?\)\({_V}\.(h[1-6]),\{{id:"[^"]*",children:"((?:[^"\\]|\\.)*)"\}}\)',
        element,
    )
    if m:
        level = int(m.group(1)[1])
        return ContentBlock(tag="heading", text=_unescape(m.group(2)), level=level)

    # Heading (compound children): (0,J.jsxs)(V.h2,{id:"...",children:[...]})
    m = re.match(
        rf'\(0,{_J}\.jsxs\)\({_V}\.(h[1-6]),\{{id:"[^"]*",children:\[(.*)\]\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        level = int(m.group(1)[1])
        text = _parse_jsxs_children(m.group(2))
        return ContentBlock(tag="heading", text=text, level=level)

    # Simple paragraph: (0,J.jsx)(V.p,{children:"..."}) or children:'...'
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:"((?:[^"\\]|\\.)*)"\}}\)$',
        element,
    )
    if m:
        return ContentBlock(tag="p", text=_unescape(m.group(1)))

    # Paragraph with single-quoted children (text contains double quotes)
    m = re.match(
        rf"\(0,{_J}\.jsx\)\({_V}\.p,\{{children:'((?:[^'\\]|\\.)*)'\}}\)$",
        element,
    )
    if m:
        return ContentBlock(tag="p", text=m.group(1))

    # Paragraph with backtick template children (text with both quote types)
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:`((?:[^`\\]|\\.)*)`\}}\)$',
        element,
    )
    if m:
        text = m.group(1).encode().decode("unicode_escape", errors="replace")
        return ContentBlock(tag="p", text=text)

    # Paragraph containing a single inline element like strong:
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:\(0,{_J}\.jsx\)\({_V}\.strong,\{{children:"((?:[^"\\]|\\.)*)"\}}\)\}}\)',
        element,
    )
    if m:
        return ContentBlock(tag="p", text=f"**{_unescape(m.group(1))}**")

    # Compound paragraph: (0,J.jsxs)(V.p,{children:[...]})
    m = re.match(
        rf'\(0,{_J}\.jsxs\)\({_V}\.p,\{{children:\[(.*)\]\}}\)$',
        element,
        re.DOTALL,
    )
    if m:
        text = _parse_jsxs_children(m.group(1))
        return ContentBlock(tag="p", text=text)

    # Image in Figure: (0,J.jsx)(v,{children:(0,J.jsx)(n,{src:VAR,alt:"..."})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\(\w,\{{children:\(0,{_J}\.jsx\)\(\w,\{{src:([\w$]+),alt:"((?:[^"\\]|\\.)*)"',
        element,
    )
    if m:
        var_name = m.group(1)
        alt = _unescape(m.group(2))
        src = img_vars.get(var_name, "")
        if src:
            return ContentBlock(tag="img", src=src, alt=alt)

    # Image with caption wrapper
    m = re.search(r'src:([\w$]+),alt:"((?:[^"\\]|\\.)*)"', element)
    if m and ('caption:' in element or 'Figure' in element or f'children:(0,{_J}.jsx)' in element
              or 'children:(0,' in element):
        var_name = m.group(1)
        alt = _unescape(m.group(2))
        src = img_vars.get(var_name, "")
        if src:
            return ContentBlock(tag="img", src=src, alt=alt)

    # Direct image: src:"...",alt:"..."
    m = re.search(r'src:"(/images/[^"]+)",alt:"((?:[^"\\]|\\.)*)"', element)
    if m:
        src = _resolve_image_url(m.group(1))
        alt = _unescape(m.group(2))
        return ContentBlock(tag="img", src=src, alt=alt)

    # Captioned figure: (0,J.jsx)(VAR,{caption:"...",children:(0,J.jsx)(VAR,{src:VAR,alt:"..."})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\(\w+,\{{caption:"([^"]*)",children:\(0,{_J}\.jsx\)\(\w+,\{{src:([\w$]+),alt:"((?:[^"\\]|\\.)*)"',
        element,
    )
    if m:
        caption = m.group(1)
        var_name = m.group(2)
        alt = _unescape(m.group(3))
        src = img_vars.get(var_name, "")
        return ContentBlock(tag="img", src=src, alt=f"{caption}: {alt}" if alt else caption)

    # Unordered/ordered list: (0,J.jsxs)(V.ul,{children:[...]})
    m = re.match(
        rf'\(0,{_J}\.jsxs?\)\({_V}\.(ul|ol),\{{children:\[(.*)\]\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        list_type = m.group(1)
        items = _parse_list_items(m.group(2))
        if items:
            return ContentBlock(tag=list_type, children=items)

    # Syntax-highlighted code block (hljs): children is an ARRAY of spans + literals
    m = re.search(
        rf'{_V}\.pre,\{{children:\(0,{_J}\.jsxs\)\({_V}\.code,\{{className:"(?:hljs )?language-(\w+)",children:\[(.*)\]\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        language = m.group(1)
        text = _extract_hljs_code_text(m.group(2))
        return ContentBlock(tag="pre", text=text, language=language)

    # Simple code block (plain string children)
    m = re.search(
        rf'{_V}\.pre,\{{children:.*?{_V}\.code,\{{(?:className:"(?:hljs )?language-(\w+)",)?children:"((?:[^"\\]|\\.)*)"',
        element,
    )
    if m:
        language = m.group(1) or ""
        text = _unescape(m.group(2))
        return ContentBlock(tag="pre", text=text, language=language)

    # Blockquote (simple string)
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.blockquote,\{{children:"((?:[^"\\]|\\.)*)"\}}\)',
        element,
    )
    if m:
        return ContentBlock(tag="blockquote", text=_unescape(m.group(1)))

    # Blockquote (compound children)
    m = re.match(
        rf'\(0,{_J}\.jsxs\)\({_V}\.blockquote,\{{children:\[(.*)\]\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        return ContentBlock(tag="blockquote", text=_parse_jsxs_children(m.group(1)))

    # HR
    if re.search(rf'{_V}\.hr', element):
        return ContentBlock(tag="hr")

    # Table: (0,J.jsx)("div",{className:"table-wrap",...})
    if 'className:"table-wrap"' in element:
        table_block = _parse_table_element(element)
        if table_block:
            return table_block

    # Info box: (0,J.jsxs)("div",{className:"info-box",children:[icon, paragraph]})
    if 'className:"info-box"' in element:
        admon_match = re.search(r'alt:"(Tip|Note|Warning|Info)"', element)
        admon_type = admon_match.group(1) if admon_match else "Note"

        text_parts = []
        for m in re.finditer(rf'{_V}\.p,\{{children:\[(.*?)\]\}}', element, re.DOTALL):
            text_parts.append(_parse_jsxs_children(m.group(1)))
        for m in re.finditer(rf'{_V}\.p,\{{children:"((?:[^"\\]|\\.)*)"\}}', element):
            text_parts.append(_unescape(m.group(1)))

        text = " ".join(t for t in text_parts if t).strip()
        text = re.sub(r'^\*\*(?:Tip|Note|Warning|Info):\*\*\s*', '', text)
        return ContentBlock(
            tag="info-box", text=text, admonition_type=admon_type
        )

    # Sample dialogue
    if 'className:"sample-dialogue"' in element:
        lines = []
        for m in re.finditer(rf'{_V}\.p,\{{children:\[(.*?)\]\}}', element, re.DOTALL):
            line_text = _parse_jsxs_children(m.group(1))
            if line_text:
                lines.append(line_text.strip())
        for m in re.finditer(rf'{_V}\.p,\{{children:"((?:[^"\\]|\\.)*)"\}}', element):
            line_text = _unescape(m.group(1))
            if line_text:
                lines.append(line_text.strip())

        return ContentBlock(tag="sample-dialogue", children=lines)

    # Log unrecognized elements for debugging new chapters
    preview = element[:120].replace('\n', ' ')
    print(f"  Warning: unrecognized element: {preview}...")
    return None


def _parse_list_items(inner: str) -> list[str]:
    """Parse list item JSX calls from a list's children."""
    items = []

    # Simple: (0,J.jsx)(V.li,{children:"..."})
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.li,\{{children:"((?:[^"\\]|\\.)*)"\}}\)', inner
    ):
        items.append(_unescape(m.group(1)))

    # Compound: (0,J.jsxs)(V.li,{children:[...]})
    for m in re.finditer(
        rf'\(0,{_J}\.jsxs\)\({_V}\.li,\{{children:\[(.*?)\]\}}\)', inner
    ):
        text = _parse_jsxs_children(m.group(1))
        if text:
            items.append(text)

    return items


def _parse_mdx_code(code: str) -> list[ContentBlock]:
    """Parse the compiled MDX JS bundle to extract content blocks in order."""
    blocks: list[ContentBlock] = []

    # Extract image variable assignments
    img_vars: dict[str, str] = {}
    for m in re.finditer(r'([\w$]+)="(/images/[^"]+)"', code):
        img_vars[m.group(1)] = _resolve_image_url(m.group(2))

    # Extract the Fragment children section
    frag_match = re.search(r'Fragment,\{children:\[(.+)\]\}\)', code, re.DOTALL)
    if not frag_match:
        return blocks

    content_js = frag_match.group(1)

    # Split into top-level elements and parse each sequentially
    elements = _split_top_level_elements(content_js)

    for element in elements:
        block = _parse_single_element(element, img_vars)
        if block is not None:
            blocks.append(block)

    return blocks


def parse_content(html: str) -> PageContent:
    """Parse HTML and extract structured content from __NEXT_DATA__."""
    data = _extract_next_data(html)
    page_props = data["props"]["pageProps"]

    if not page_props:
        raise ValueError(
            "Empty pageProps — chapter may be paywalled. "
            "Ensure a valid token cookie is present."
        )

    title = page_props.get("title", "Untitled")
    chapter_number = page_props.get("chapter", 1)
    code = page_props.get("code", "")

    blocks = _parse_mdx_code(code)

    return PageContent(
        title=title,
        chapter_number=chapter_number,
        blocks=blocks,
    )
