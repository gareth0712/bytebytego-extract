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
    chapter_number: int | str  # int for OOD course; "01-00" style str for Coding Patterns
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


def _extract_katex_text(katex_span: str) -> str:
    """Extract the LaTeX source from a KaTeX span.

    KaTeX renders math via two representations:
    - katex-mathml: MathML (for accessibility/screen readers)
    - katex-html: Visual HTML rendering

    Both are derived from the LaTeX source stored in an annotation element:
      e.annotation,{encoding:"application/x-tex",children:"O(n^2)"}

    The children value may use double-quoted, single-quoted, or backtick
    template literals. Multi-line formulas (e.g. aligned environments) use
    backtick literals because they contain newlines and both quote types.
    """
    # Double-quoted annotation (most common)
    m = re.search(
        r'encoding:"application/x-tex",children:"((?:[^"\\]|\\.)*)"',
        katex_span,
    )
    if m:
        latex = _unescape(m.group(1))
        return f"${latex.strip()}$"

    # Backtick template literal annotation (used for multi-line/aligned formulas)
    m = re.search(
        r'encoding:"application/x-tex",children:`((?:[^`\\]|\\.)*)`',
        katex_span,
        re.DOTALL,
    )
    if m:
        latex = m.group(1)
        # Backtick literals use \\\\n for newlines in the raw JS source;
        # decode python escape sequences to get the actual LaTeX string.
        latex = latex.encode().decode("unicode_escape", errors="replace")
        return f"${latex.strip()}$"

    # Single-quoted annotation (rare; text contains double quotes)
    m = re.search(
        r"encoding:\"application/x-tex\",children:'((?:[^'\\]|\\.)*)'",
        katex_span,
    )
    if m:
        latex = m.group(1)
        return f"${latex.strip()}$"

    return ""


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

    # Footnote superscripts: (0,J.jsx)(V.sup,{children:...}) — claim entire range, emit nothing.
    # Footnote refs contain HTML attribute names/values that would leak as prose text.
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.sup,\{{children:',
        inner,
    ):
        # Find the matching closing paren for the sup JSX call
        start = m.start()
        first_close = inner.find(')', start) + 1  # end of (0,J.jsx)
        d = 0
        end = first_close
        for idx in range(first_close, min(first_close + 2000, len(inner))):
            if inner[idx] == '(':
                d += 1
            elif inner[idx] == ')':
                d -= 1
                if d == 0:
                    end = idx + 1
                    break
        jsx_parts.append((start, end, ""))  # claim range, emit nothing

    # KaTeX inline math: (0,J.jsxs)(V.span,{className:"katex",children:[...]})
    # Extract the LaTeX annotation source and render as $...$
    for m in re.finditer(
        rf'\(0,{_J}\.jsxs\)\({_V}\.span,\{{className:"katex",children:\[',
        inner,
    ):
        # Find the matching closing bracket for this span's children array
        start = m.start()
        bracket_start = m.end() - 1  # position of opening [
        depth = 0
        end = bracket_start
        for k in range(bracket_start, len(inner)):
            if inner[k] == '[':
                depth += 1
            elif inner[k] == ']':
                depth -= 1
                if depth == 0:
                    end = k
                    break
        # The full katex span ends with ]}) after the children array
        full_end = inner.find('})', end)
        if full_end == -1:
            continue
        full_end += 2  # include )}
        katex_span = inner[start:full_end]
        latex_text = _extract_katex_text(katex_span)
        if latex_text:
            jsx_parts.append((start, full_end, latex_text))

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
    # Compound children array (checked before simple string to avoid matching
    # a nested child's children:"..." instead of the top-level one):
    # children:[(0,J.jsx)(V.p,{...}),(0,J.jsx)(V.p,{...}),...]
    array_m = re.search(r'^children:\[', cell_content)
    if array_m:
        # Compound children with "div" wrappers (raw-string table cells):
        # children:[(0,J.jsx)("div",{children:"text"}),...]
        # Extract all "div" children strings and join with newline
        if '"div"' in cell_content:
            div_texts = []
            for m in re.finditer(
                rf'\(0,{_J}\.jsx\)\("div",\{{children:"((?:[^"\\]|\\.)*)"\}}\)',
                cell_content,
            ):
                div_texts.append(_unescape(m.group(1)))
            if div_texts:
                return "\n".join(div_texts)
        # Block-level paragraph children: children:[(0,J.jsx)(V.p,{...}),...]
        # Extract each p element individually so they are separated properly.
        # Use brace-depth tracking to extract each p's props reliably, avoiding
        # issues with lazy (.*?) matching on nested JSX like strong/em/code.
        inner_m = re.search(r'children:\[(.*)\]', cell_content, re.DOTALL)
        if inner_m:
            inner = inner_m.group(1)
            para_texts: list[str] = []
            for pm in re.finditer(
                rf'\(0,{_J}\.jsx[s]?\)\({_V}\.p,',
                inner,
            ):
                # pm.end() should point to '{' of the props object
                brace_pos = pm.end()
                if brace_pos >= len(inner) or inner[brace_pos] != "{":
                    continue
                props_str = _extract_brace_content(inner, brace_pos)
                if props_str is None:
                    continue
                # props_str is the full props dict content, e.g. children:"..." or
                # children:(0,J.jsx)(e.strong,...) or children:[...]
                children_m = re.match(r'children:(.*)', props_str, re.DOTALL)
                if not children_m:
                    continue
                p_children = children_m.group(1).rstrip(", \t\n")
                # Simple double-quoted string
                sm = re.match(r'"((?:[^"\\]|\\.)*)"$', p_children)
                if sm:
                    para_texts.append(_unescape(sm.group(1)))
                    continue
                # Array children (e.g. [strong, text, em])
                am = re.match(r'\[(.*)\]$', p_children, re.DOTALL)
                if am:
                    para_texts.append(_parse_jsxs_children(am.group(1)))
                    continue
                # Single nested JSX child (e.g. a strong element)
                para_texts.append(_parse_jsxs_children(p_children))
            if para_texts:
                return "\n\n".join(para_texts)
            return _parse_jsxs_children(inner)
    # Simple string: children:"text"
    m = re.search(r'children:"((?:[^"\\]|\\.)*)"', cell_content)
    if m:
        return _unescape(m.group(1))
    # Fallback: compound children without leading-array anchor
    m = re.search(r'children:\[(.*?)\]', cell_content, re.DOTALL)
    if m:
        return _parse_jsxs_children(m.group(1))
    return ""


def _extract_cell_props(text: str, tag: str) -> list[str]:
    """Extract all th or td cell props strings from ``text`` using brace-depth tracking.

    Finds every ``(0,J.jsx[s])(V.<tag>,{...})`` call and returns the content
    of the outer braces (the props dict) for each.  This handles nested JSX
    correctly — unlike lazy ``(.*?)`` regex which stops at the first ``}``.
    """
    results: list[str] = []
    # Match the opening of each th/td call; .end() points just before the '{'.
    pattern = re.compile(rf'\(0,{_J}\.jsx[s]?\)\({_V}\.{tag},', re.DOTALL)
    for m in pattern.finditer(text):
        brace_pos = m.end()
        if brace_pos >= len(text) or text[brace_pos] != "{":
            continue
        props = _extract_brace_content(text, brace_pos)
        if props is not None:
            results.append(props)
    return results


def _extract_rows_from_section(section: str) -> list[list[str]]:
    """Extract table rows (list of cell text strings) from a tbody section string.

    Handles both array-children ``tr`` (``children:[...]``) and single-child
    ``tr`` (``children:(0,J.jsx)(...)``) by using brace-depth tracking to
    extract each ``tr``'s props, then extracting ``td`` cells from those props.
    """
    rows: list[list[str]] = []
    # Find every tr call; use _extract_brace_content for its props to avoid
    # the lazy-regex pitfall when td cells contain nested JSX.
    tr_pattern = re.compile(rf'\(0,{_J}\.jsx[s]?\)\({_V}\.tr,', re.DOTALL)
    for m in tr_pattern.finditer(section):
        brace_pos = m.end()
        if brace_pos >= len(section) or section[brace_pos] != "{":
            continue
        tr_props = _extract_brace_content(section, brace_pos)
        if tr_props is None:
            continue
        cells = [_parse_table_cell(p) for p in _extract_cell_props(tr_props, "td")]
        if cells:
            rows.append(cells)
    return rows


def _parse_table_element(element: str) -> ContentBlock | None:
    """Parse a table-wrap div element into a ContentBlock.

    Handles both array children ``children:[...]`` and single-child
    ``children:(0,J.jsx)(...)`` patterns, which React uses when there is
    only one child (e.g. a single-row/single-cell table).
    """
    headers: list[str] = []
    rows: list[list[str]] = []

    # --- Extract header cells from thead ---
    # Find the thead JSX call and extract its props with brace-depth tracking,
    # then search those props for any th cells (also brace-depth-tracked).
    thead_pattern = re.compile(rf'\(0,{_J}\.jsx[s]?\)\({_V}\.thead,', re.DOTALL)
    m = thead_pattern.search(element)
    if m:
        thead_brace = m.end()
        if thead_brace < len(element) and element[thead_brace] == "{":
            thead_props = _extract_brace_content(element, thead_brace)
            if thead_props is not None:
                headers = [_parse_table_cell(p) for p in _extract_cell_props(thead_props, "th")]

    # --- Extract body rows from tbody ---
    tbody_pattern = re.compile(rf'\(0,{_J}\.jsx[s]?\)\({_V}\.tbody,', re.DOTALL)
    m = tbody_pattern.search(element)
    if m:
        tbody_brace = m.end()
        if tbody_brace < len(element) and element[tbody_brace] == "{":
            tbody_props = _extract_brace_content(element, tbody_brace)
            if tbody_props is not None:
                rows = _extract_rows_from_section(tbody_props)

    if headers or rows:
        return ContentBlock(tag="table", headers=headers, children=rows)
    return None


def _extract_brace_content(text: str, start: int) -> str | None:
    """Return the content between the ``{`` at ``start`` and its matching ``}``.

    Tracks nested braces and ignores ``{``/``}`` inside string literals
    (single-quoted, double-quoted, and backtick strings).  Returns the text
    between the outer braces (exclusive), or ``None`` if unbalanced.
    """
    if start >= len(text) or text[start] != "{":
        return None
    depth = 0
    in_str: str | None = None  # current string delimiter, or None
    i = start
    while i < len(text):
        ch = text[i]
        if in_str:
            if ch == "\\" and in_str != "`":
                i += 2  # skip escaped char
                continue
            if ch == in_str:
                in_str = None
        else:
            if ch in ('"', "'", "`"):
                in_str = ch
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    return text[start + 1 : i]
        i += 1
    return None


def _parse_inside_out_table(element: str) -> ContentBlock | None:
    """Parse an inside-out table element into a ContentBlock.

    Inside-out tables use raw string element names ("tbody", "tr", "td")
    instead of namespace references (V.tbody, V.tr, V.td). They have no
    thead, so all rows are treated as body rows with no header row.

    The "tr" may wrap a single "td" child without an array (i.e. the tr uses
    ``jsx`` not ``jsxs`` and writes ``children:(0,...)``) so the old row regex
    ``children:[...]`` never matched.  The fix locates every ``"td"`` call via
    the regex match position, then uses brace-depth tracking to reliably
    extract the full props dict, even when it contains deeply nested JSX.
    """
    rows: list[list[str]] = []

    # The regex matches "(0,t.jsx[s]?)("td"," and ends right after the comma,
    # so m.end() points to the opening "{" of the props dict.
    for m in re.finditer(rf'\(0,{_J}\.jsx[s]?\)\("td",', element):
        brace_start = m.end()  # should be the '{' of the props object
        if brace_start >= len(element) or element[brace_start] != "{":
            continue
        props_content = _extract_brace_content(element, brace_start)
        if props_content is None:
            continue
        cell_text = _parse_table_cell(props_content)
        rows.append([cell_text])

    if rows:
        return ContentBlock(tag="table", headers=[], children=rows)
    return None


def _parse_raw_string_table(element: str) -> ContentBlock | None:
    """Parse a table-wrap that uses raw string element names ("table", "thead", "tbody", "tr", "td").

    This variant appears in the ML System Design course. Instead of namespace
    references (t.table, t.thead, etc.), it uses raw string tag names in quotes.
    Headers come from "td" cells inside "thead" (often wrapping a "strong" element),
    and body cells may contain nested "div" children with bullet-prefixed text.
    """
    headers: list[str] = []
    rows: list[list[str]] = []

    # Extract headers from thead > tr > td (using raw string "td", not t.th)
    thead_match = re.search(
        rf'"thead",\{{children:.*?"tr",\{{children:\[(.*?)\]\}}',
        element, re.DOTALL,
    )
    if thead_match:
        for m in re.finditer(
            rf'\(0,{_J}\.jsx[s]?\)\("td",\{{(.*?)\}}\)',
            thead_match.group(1), re.DOTALL,
        ):
            # Header cells often wrap text in "strong": children:(0,J.jsx)("strong",{children:"text"})
            cell_inner = m.group(1)
            strong_m = re.search(r'"strong",\{children:"((?:[^"\\]|\\.)*)"\}', cell_inner)
            if strong_m:
                headers.append(_unescape(strong_m.group(1)))
            else:
                headers.append(_parse_table_cell(cell_inner))

    # Extract body rows from tbody > tr > td
    tbody_match = re.search(
        rf'"tbody",\{{children:\[(.*)\]\}}',
        element, re.DOTALL,
    )
    if tbody_match:
        for row_match in re.finditer(
            rf'\(0,{_J}\.jsxs?\)\("tr",\{{children:\[(.*?)\]\}}\)',
            tbody_match.group(1), re.DOTALL,
        ):
            cells = []
            for td_match in re.finditer(
                rf'\(0,{_J}\.jsx[s]?\)\("td",\{{(.*?)\}}\)',
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

    # Skip bare string literals that are just whitespace (e.g. " " separators between elements)
    if re.match(r'^["\'][\s]*["\']$', element.strip()):
        return None

    # Skip interactive/UI-only components with no extractable content:
    # OpenCodeEditor: (0,J.jsx)(r,{}) — an embedded code editor widget
    # Any single-letter component called with empty props is a UI-only widget
    if re.match(rf'^\(0,{_J}\.jsx\)\([a-zA-Z],\{{\}}\)$', element.strip()):
        return None

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

    # Heading (single nested child): (0,J.jsx)(V.h2,{id:"...",children:(0,J.jsx)(V.strong,{...})})
    # React uses jsx (not jsxs) when there is exactly one child element rather than an array.
    # Extract the children value and parse it as inline JSX.
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.(h[1-6]),\{{id:"[^"]*",children:(\(0,{_J}\.jsx[s]?\).*)\}}\)',
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

    # Paragraph containing a single inline element like strong (simple string):
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:\(0,{_J}\.jsx\)\({_V}\.strong,\{{children:"((?:[^"\\]|\\.)*)"\}}\)\}}\)',
        element,
    )
    if m:
        return ContentBlock(tag="p", text=f"**{_unescape(m.group(1))}**")

    # Paragraph containing a compound strong (mixed text + inline code):
    # (0,J.jsx)(V.p,{children:(0,J.jsxs)(V.strong,{children:[...]})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:\(0,{_J}\.jsxs\)\({_V}\.strong,\{{children:\[(.*)\]\}}\)\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        inner_text = _parse_jsxs_children(m.group(1))
        return ContentBlock(tag="p", text=f"**{inner_text}**")

    # Paragraph containing a single em child (double-quoted):
    # (0,J.jsx)(V.p,{children:(0,J.jsx)(V.em,{children:"..."})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:\(0,{_J}\.jsx\)\({_V}\.em,\{{children:"((?:[^"\\]|\\.)*)"\}}\)\}}\)',
        element,
    )
    if m:
        return ContentBlock(tag="p", text=f"_{_unescape(m.group(1))}_")

    # Paragraph containing a single em child (single-quoted):
    # (0,J.jsx)(V.p,{children:(0,J.jsx)(V.em,{children:'...'})})
    m = re.match(
        rf"\(0,{_J}\.jsx\)\({_V}\.p,\{{children:\(0,{_J}\.jsx\)\({_V}\.em,\{{children:'((?:[^'\\]|\\.)*)'\}}\)\}}\)",
        element,
    )
    if m:
        return ContentBlock(tag="p", text=f"_{m.group(1)}_")

    # Paragraph containing a single em child (backtick template):
    # (0,J.jsx)(V.p,{children:(0,J.jsx)(V.em,{children:`...`})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:\(0,{_J}\.jsx\)\({_V}\.em,\{{children:`((?:[^`\\]|\\.)*)`\}}\)\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        text = m.group(1).encode().decode("unicode_escape", errors="replace")
        return ContentBlock(tag="p", text=f"_{text}_")

    # Compound paragraph: (0,J.jsxs)(V.p,{children:[...]})
    m = re.match(
        rf'\(0,{_J}\.jsxs\)\({_V}\.p,\{{children:\[(.*)\]\}}\)$',
        element,
        re.DOTALL,
    )
    if m:
        text = _parse_jsxs_children(m.group(1))
        return ContentBlock(tag="p", text=text)

    # Standalone KaTeX paragraph: (0,J.jsx)(V.p,{children:(0,J.jsxs)(V.span,{className:"katex",...})})
    # These are section-label headings rendered as math (e.g. underline{text{Case 1: ...}})
    if re.match(rf'\(0,{_J}\.jsx\)\({_V}\.p,\{{children:', element) and 'className:"katex"' in element:
        latex_text = _extract_katex_text(element)
        if latex_text:
            return ContentBlock(tag="p", text=latex_text)

    # Block-level KaTeX display math: (0,J.jsx)(V.span,{className:"katex-display",children:...})
    # These are standalone display math formulas (rendered with $$...$$ in Markdown).
    if 'className:"katex-display"' in element:
        latex_text = _extract_katex_text(element)
        if latex_text:
            # Convert inline $...$ to display $$...$$ by unwrapping and rewrapping
            inner = latex_text[1:-1] if latex_text.startswith("$") and latex_text.endswith("$") else latex_text
            return ContentBlock(tag="p", text=f"$$\n{inner}\n$$")

    # Image in Figure: (0,J.jsx)(v,{children:(0,J.jsx)(n,{src:VAR,...,alt:"..."})})
    # Allows extra props (width, height, priority, etc.) between src and alt.
    m = re.match(
        rf'\(0,{_J}\.jsx\)\(\w,\{{children:\(0,{_J}\.jsx\)\(\w,\{{src:([\w$]+)',
        element,
    )
    if m:
        var_name = m.group(1)
        alt_m = re.search(r',alt:"((?:[^"\\]|\\.)*)"', element)
        alt = _unescape(alt_m.group(1)) if alt_m else ""
        src = img_vars.get(var_name, "")
        if src:
            return ContentBlock(tag="img", src=src, alt=alt)

    # Image with caption wrapper — src may have extra props (width, height) before alt
    src_m = re.search(r'src:([\w$]+)', element)
    alt_m = re.search(r',alt:"((?:[^"\\]|\\.)*)"', element)
    if src_m and alt_m and (
        'caption:' in element or 'Figure' in element
        or f'children:(0,{_J}.jsx)' in element or 'children:(0,' in element
    ):
        var_name = src_m.group(1)
        alt = _unescape(alt_m.group(1))
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

    # Center-wrapped text label with inline KaTeX: (0,J.jsxs)("center",{children:["text", katex_span, ...]})
    # Used for figure captions that mix plain text and math (e.g. "Rotation matrix R_{θ,m}^d")
    # Must be checked BEFORE the center+image handlers to avoid falling through.
    m = re.match(
        rf'\(0,{_J}\.jsxs?\)\("center",\{{children:\[',
        element,
    )
    if m and 'src:' not in element and 'caption:' not in element:
        # Extract the children array content and parse as mixed text+inline math
        bracket_start = element.index('[', m.start())
        depth = 0
        end = bracket_start
        for k in range(bracket_start, len(element)):
            if element[k] == '[':
                depth += 1
            elif element[k] == ']':
                depth -= 1
                if depth == 0:
                    end = k
                    break
        children_inner = element[bracket_start + 1:end]
        text = _parse_jsxs_children(children_inner)
        if text.strip():
            return ContentBlock(tag="p", text=text)

    # Center-wrapped captioned figure: (0,J.jsx)("center",{children:(0,J.jsx)(VAR,{caption:"...",children:(0,J.jsx)(VAR,{src:VAR,alt:"..."})})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\("center",\{{children:\(0,{_J}\.jsx\)\(\w+,\{{caption:"([^"]*)",children:\(0,{_J}\.jsx\)\(\w+,\{{src:([\w$]+),alt:"((?:[^"\\]|\\.)*)"',
        element,
    )
    if m:
        caption = m.group(1)
        var_name = m.group(2)
        alt = _unescape(m.group(3))
        src = img_vars.get(var_name, "")
        return ContentBlock(tag="img", src=src, alt=f"{caption}: {alt}" if alt else caption)

    # Center-wrapped captioned figure with "div" size wrapper:
    # (0,J.jsx)("center",{children:(0,J.jsx)(VAR,{caption:"...",children:(0,J.jsx)("div",{style:{...},children:(0,J.jsx)(VAR,{src:VAR,...,alt:"..."})})})})
    # Used in ML course for fixed-dimension images (e.g. figure 1.13, shadow deployment diagrams).
    if '"center"' in element and 'caption:' in element:
        cap_m = re.search(r'caption:"([^"]*)"', element)
        src_m = re.search(r'src:([\w$]+)', element)
        alt_m = re.search(r',alt:"((?:[^"\\]|\\.)*)"', element)
        if cap_m and src_m and alt_m:
            caption = cap_m.group(1)
            var_name = src_m.group(1)
            alt = _unescape(alt_m.group(1))
            src = img_vars.get(var_name, "")
            return ContentBlock(tag="img", src=src, alt=f"{caption}: {alt}" if alt else caption)

    # Center-wrapped table: (0,J.jsx)("center",{children:(0,J.jsxs)(V.table,{children:[...]})})
    # Strip the "center" wrapper and delegate to the standard table parser.
    if '"center"' in element and ('thead' in element or 'table' in element) and 'src:' not in element:
        table_block = _parse_table_element(element)
        if table_block:
            return table_block

    # Bare namespace-ref table: (0,J.jsxs)(V.table,{children:[...]})
    # Appears when the table is not wrapped in a "table-wrap" div (e.g. Coding Patterns ch11-07).
    # Uses namespace refs like e.table, e.thead, e.tr — handled by _parse_table_element.
    if re.match(rf'\(0,{_J}\.jsxs?\)\({_V}\.table,\{{', element) and (
        'thead' in element or 'tbody' in element
    ):
        table_block = _parse_table_element(element)
        if table_block:
            return table_block

    # Unordered/ordered list: (0,J.jsxs)(V.ul,{children:[...]})
    # Also handles ol with start attribute: (0,J.jsxs)(V.ol,{start:"2",children:[...]})
    m = re.match(
        rf'\(0,{_J}\.jsxs?\)\({_V}\.(ul|ol),\{{(?:[^{{}}]*?,)?children:\[(.*)\]\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        list_type = m.group(1)
        items = _parse_list_items(m.group(2))
        if items:
            return ContentBlock(tag=list_type, children=items)

    # Empty code block: (0,J.jsx)(V.pre,{children:(0,J.jsx)(V.code,{})}) — skip silently
    if re.search(rf'{_V}\.pre,\{{children:\(0,{_J}\.jsx\)\({_V}\.code,\{{\}}\)\}}', element):
        return None

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

    # Simple code block (backtick template children)
    m = re.search(
        rf'{_V}\.pre,\{{children:.*?{_V}\.code,\{{(?:className:"(?:hljs )?language-(\w+)",)?children:`((?:[^`\\]|\\.)*)`',
        element,
        re.DOTALL,
    )
    if m:
        language = m.group(1) or ""
        text = m.group(2)
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

    # Table caption paragraph (two variants):
    # 1. Direct string:  (0,J.jsx)("p",{className:"tableCaption",children:"..."})
    # 2. Nested t.p:     (0,J.jsx)("p",{className:"tableCaption",children:(0,J.jsx)(V.p,{children:"..."})})
    if 'className:"tableCaption"' in element:
        # Try nested variant first
        m = re.search(
            rf'{_J}\.jsx\)\({_V}\.p,\{{children:"((?:[^"\\]|\\.)*)"\}}\)',
            element,
        )
        if not m:
            # Direct string variant
            m = re.search(r'className:"tableCaption",children:"((?:[^"\\]|\\.)*)"', element)
        if m:
            return ContentBlock(tag="p", text=f"*{_unescape(m.group(1))}*")

    # Table: (0,J.jsx)("div",{className:"table-wrap",...})
    # Also handles "code-table" wrapper (Mobile System Design variant).
    # Allow optional extra properties (e.g. style:{...}) between className and children.
    if re.search(r'className:"(?:table-wrap|mdx-table-wrap|code-table)".*?children:', element):
        # First try namespace-ref table (t.table/t.thead/t.tbody)
        table_block = _parse_table_element(element)
        if table_block:
            return table_block
        # Fall back to raw-string table ("table"/"thead"/"tbody") — ML course variant
        table_block = _parse_raw_string_table(element)
        if table_block:
            return table_block

    # Inside-out table: (0,J.jsx)("table",{className:"inside-out",...})
    # Used for side-by-side comparison layouts. Uses raw string element names
    # ("tbody", "tr", "td") instead of namespace refs (V.tbody etc.).
    if 'className:"inside-out"' in element:
        table_block = _parse_inside_out_table(element)
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

    # Note block: (0,J.jsxs)("div",{className:"note-block",...}) or {class:"note-block",...}
    # The `class` (not `className`) variant appears in Mobile System Design ch8+.
    if 'className:"note-block"' in element or 'class:"note-block"' in element:
        text_parts = []
        for m in re.finditer(rf'{_V}\.p,\{{children:\[(.*?)\]\}}', element, re.DOTALL):
            text_parts.append(_parse_jsxs_children(m.group(1)))
        for m in re.finditer(rf'{_V}\.p,\{{children:"((?:[^"\\]|\\.)*)"\}}', element):
            text_parts.append(_unescape(m.group(1)))

        text = " ".join(t for t in text_parts if t).strip()
        text = re.sub(r'^\*\*(?:Tip|Note|Warning|Info|[^*]+):\*\*\s*', '', text)
        return ContentBlock(
            tag="info-box", text=text, admonition_type="Note"
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

    # Footnotes section: (0,J.jsxs)(V.section,{"data-footnotes":true,className:"footnotes",...})
    # These are supplementary reference anchors appended by MDX; main text is complete without them.
    if 'className:"footnotes"' in element and 'data-footnotes' in element:
        return None

    # Bare line break: (0,J.jsx)("br",{}) — skip silently
    if re.match(rf'\(0,{_J}\.jsx\)\("br",\{{\}}\)', element.strip()):
        return None

    # Bare "span" wrapper around a paragraph — transparent container; unwrap and parse.
    # (0,J.jsx)("span",{children:(0,J.jsx)(V.p,{children:"..."})})
    m = re.match(
        rf'\(0,{_J}\.jsx\)\("span",\{{children:\(0,{_J}\.jsx[s]?\)\({_V}\.p,\{{(.*)\}}\)\}}\)',
        element,
        re.DOTALL,
    )
    if m:
        inner_props = m.group(1)
        # Simple double-quoted children
        sm = re.match(r'children:"((?:[^"\\]|\\.)*)"$', inner_props)
        if sm:
            return ContentBlock(tag="p", text=_unescape(sm.group(1)))
        # Array children
        am = re.match(r'children:\[(.*)\]$', inner_props, re.DOTALL)
        if am:
            return ContentBlock(tag="p", text=_parse_jsxs_children(am.group(1)))
        # Fallback: parse whatever is in children:
        cm = re.match(r'children:(.*)', inner_props, re.DOTALL)
        if cm:
            return ContentBlock(tag="p", text=_parse_jsxs_children(cm.group(1)))

    # Log unrecognized elements for debugging new chapters
    preview = element[:120].replace('\n', ' ')
    print(f"  Warning: unrecognized element: {preview}...")
    return None


def _parse_list_items(inner: str) -> list[str]:
    """Parse list item JSX calls from a list's children.

    Collects all matches with their start positions and sorts by position so
    that mixed lists (some simple, some compound items) preserve document order.
    """
    # (start_pos, text) pairs collected from all three patterns
    matches: list[tuple[int, str]] = []

    # Simple double-quoted: (0,J.jsx)(V.li,{children:"..."})
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.li,\{{children:"((?:[^"\\]|\\.)*)"\}}\)', inner
    ):
        matches.append((m.start(), _unescape(m.group(1))))

    # Simple single-quoted: (0,J.jsx)(V.li,{children:'...'})
    # Used when the li text itself contains double-quote characters.
    for m in re.finditer(
        rf"\(0,{_J}\.jsx\)\({_V}\.li,\{{children:'((?:[^'\\]|\\.)*)'\}}\)", inner
    ):
        matches.append((m.start(), _unescape(m.group(1))))

    # Compound: (0,J.jsxs)(V.li,{children:[...]})
    # re.DOTALL is required: li children may contain backtick template literals
    # or quoted strings with embedded newlines (e.g. from multi-line compiled JSX).
    for m in re.finditer(
        rf'\(0,{_J}\.jsxs\)\({_V}\.li,\{{children:\[(.*?)\]\}}\)', inner, re.DOTALL
    ):
        text = _parse_jsxs_children(m.group(1))
        if text:
            matches.append((m.start(), text))

    # Single inline JSX child: (0,J.jsx)(V.li,{children:(0,J.jsx)(V.code,{...})})
    # Used when the li contains exactly one inline element (code, strong, em, a, etc.)
    # rather than a plain string or an array. Treat children: value as inline content.
    for m in re.finditer(
        rf'\(0,{_J}\.jsx\)\({_V}\.li,\{{children:\(0,{_J}\.jsx[s]?\)\({_V}\.\w+,\{{(.*?)\}}\)\}}\)',
        inner,
        re.DOTALL,
    ):
        # Use _parse_jsxs_children on the full children: JSX call (from the opening '(' onwards)
        li_start = m.start()
        # Extract the children: value (everything after 'children:' up to the matching closing paren)
        children_val = m.group(0)
        # Find the start of the inner JSX call: 'children:(0,...'
        inner_jsx_start = children_val.index('children:(') + len('children:')
        inner_jsx = children_val[inner_jsx_start:]
        # Strip exactly the trailing '})' from the outer li — these are the closing
        # brace+paren of the li's props object and the li JSX call itself.
        # Do NOT use rstrip(')}') as that would greedily strip closing chars from
        # the inner element (e.g. the '}' and ')' of an anchor's closing '})').
        if inner_jsx.endswith('})'):
            inner_jsx = inner_jsx[:-2]
        if not any(s == li_start for s, _ in matches):
            text = _parse_jsxs_children(inner_jsx)
            if text:
                matches.append((li_start, text))

    matches.sort(key=lambda t: t[0])
    return [text for _, text in matches]


def _split_wrapper_children(children_str: str) -> list[str]:
    """Split a JSX children array string into individual child elements.

    Unlike _split_top_level_elements (which looks for backtick separators),
    this splits on commas at paren-depth 0 — suitable for wrapper component
    children arrays where elements are separated by `,` not `,`\\n``.
    """
    elements: list[str] = []
    current: list[str] = []
    depth = 0
    bracket_depth = 0
    i = 0
    n = len(children_str)

    while i < n:
        ch = children_str[i]

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
        elif ch == '"':
            # Skip string literals
            current.append(ch)
            i += 1
            while i < n and children_str[i] != '"':
                if children_str[i] == '\\':
                    current.append(children_str[i])
                    i += 1
                    if i < n:
                        current.append(children_str[i])
                        i += 1
                    continue
                current.append(children_str[i])
                i += 1
            if i < n:
                current.append(children_str[i])  # closing quote
        elif ch == '`':
            # Consume backtick template literal
            current.append(ch)
            i += 1
            while i < n and children_str[i] != '`':
                current.append(children_str[i])
                i += 1
            if i < n:
                current.append(children_str[i])  # closing backtick
        elif ch == ',' and depth == 0 and bracket_depth == 0:
            # Top-level comma — element boundary
            text = "".join(current).strip()
            if text:
                elements.append(text)
            current = []
            i += 1
            continue
        else:
            current.append(ch)

        i += 1

    text = "".join(current).strip()
    if text:
        elements.append(text)

    return elements


def _extract_wrapper_children(element: str) -> list[str] | None:
    """If element is a transparent single-letter custom component wrapper
    (e.g. ProblemDescription, CodeTabs used as a prose wrapper), return the
    list of child element strings so they can be parsed inline.

    Pattern: (0,J.jsxs)(SINGLE_LETTER,{children:[...]})
    where SINGLE_LETTER has no dot (not a namespace ref like e.p).

    Returns None if the element doesn't match this pattern.
    """
    m = re.match(
        rf'\(0,{_J}\.jsxs\)\(([a-zA-Z]),\{{(?:[^{{}}]*?,)?children:\[',
        element,
    )
    if not m:
        return None
    # Find the opening bracket of children:[
    bracket_start = element.index('children:[') + len('children:[') - 1
    # Track brackets to find the matching ]
    depth = 0
    for i in range(bracket_start, len(element)):
        ch = element[i]
        if ch == '[':
            depth += 1
        elif ch == ']':
            depth -= 1
            if depth == 0:
                inner = element[bracket_start + 1:i]
                return _split_wrapper_children(inner)
    return None


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
        # Transparent wrapper components (e.g. ProblemDescription) contain
        # multiple child elements that should be parsed inline. Expand them.
        child_elements = _extract_wrapper_children(element)
        if child_elements is not None:
            for child in child_elements:
                child_block = _parse_single_element(child, img_vars)
                if child_block is not None:
                    blocks.append(child_block)
            continue

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
