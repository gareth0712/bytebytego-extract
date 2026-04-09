# ByteByteGo Course Extractor

## What This Is

A Python CLI tool that extracts content from ByteByteGo course pages and exports to Markdown and PDF.

```bash
python __main__.py <url> [--output-dir DIR] [--cookies FILE] [--dump-json]
python __main__.py <url> --all [--output-dir DIR] [--cookies FILE] [--dump-json]
```

## Architecture

```
__main__.py            # Entry point / CLI — single-chapter or batch (--all) extraction
fetcher.py             # Fetches HTML, extracts __NEXT_DATA__ JSON, parses compiled MDX
markdown_converter.py  # Converts ContentBlock list → Markdown file
pdf_exporter.py        # Converts ContentBlock list → styled PDF (ReportLab + svglib)
requirements.txt       # requests, beautifulsoup4, reportlab, svglib
Reference.md           # Detailed research notes on MDX internals (read if debugging parser)
responses/             # Test fixtures (raw JSON from live pages, for offline development)
  nextdata/ch01.json   # Ch1 pageProps (free)
  nextdata/ch02.json   # Ch2 pageProps (free)
  nextdata/ch03.json   # Ch3 pageProps (paid, captured while authenticated)
  paywalled/ch03_no_auth.json  # What ch3 returns without cookies (empty {})
  toc.json             # Full table of contents for the OOD course
```

## Course Coverage

186 chapters across 6 courses — all extracted:

| Course                           | Chapters | Status    |
| -------------------------------- | -------- | --------- |
| Object-Oriented Design Interview | 14/14    | Complete  |
| Coding Patterns                  | 120/120  | Complete  |
| Tech Resume                      | 19/19    | Complete  |
| ML System Design Interview       | 11/11    | Complete  |
| Mobile System Design Interview   | 11/11    | Complete  |
| GenAI System Design Interview    | 11/11    | Complete  |
| **Total**                        | **186**  | **Done**  |

Output: `output/{course-slug}/{chapter-number}. {title}.{md,pdf}`

## How ByteByteGo Pages Work (Critical Context)

ByteByteGo uses **Next.js with compiled MDX**. The page HTML does NOT contain rendered content — it's a blank shell. All content lives inside a `<script id="__NEXT_DATA__">` JSON blob, in `props.pageProps.code`, which is a **compiled JS module** containing JSX calls.

### **NEXT_DATA** structure

```
props.pageProps:
  .title    → "What is an Object-Oriented Design Interview"
  .chapter  → 1
  .course   → "object-oriented-design-interview"
  .free     → true/false (paywalled chapters return empty pageProps without cookies)
  .code     → compiled MDX JS string (the actual content)
  .toc      → list of all chapters [{title, slug, chapter, free}, ...]
```

Note: `parse_content()` only reads `.title`, `.chapter`, and `.code`. The `.toc` field is used by `extract_toc()` for batch mode. The `.free` and `.course` fields are metadata — useful for debugging but not parsed.

### Authentication

Paywalled chapters return **empty `pageProps`** (`{}`) without authentication. The fetcher auto-loads cookies from a Netscape-format file. Resolution order:

1. `--cookies FILE` CLI argument
2. `BYTEBYTEGO_COOKIES` environment variable
3. Default: `S:\program-files\cookies\cookies-bytebytego.txt`

The `token` cookie is a Firebase JWT that **expires hourly**. To refresh: log in to bytebytego.com in Chrome, re-export cookies with a browser extension.

**Error when cookies are missing/expired:**

```
ValueError: Empty pageProps — chapter may be paywalled. Ensure a valid token cookie is present.
```

**Error when pageProps exists but code is empty:**
The script will produce an MD/PDF with just the title and no content blocks. Check `Content blocks: 0` in the output.

### Compiled MDX format

The `.code` field is a self-executing JS module. Content is inside a `Fragment,{children:[...]}` array of JSX calls separated by `` ,`\n` `` backtick separators.

#### CRITICAL: Variable name instability

There are TWO variable names that change between articles:

1. **JSX caller** (`_J` in code): `e.jsx` in ch1-2, `C.jsx` in ch3-4
2. **Component namespace** (`_V` in code): `C.p` in ch1, `i.p` in ch2, `e.p` in ch3-4

All regexes use `\w+` for both positions. **Never hardcode a single letter.**

### String encoding

Three types of string literals appear in the compiled MDX, all must be handled:

1. **Double-quoted**: `"text with \u2019 escapes"` — most common
2. **Single-quoted**: `'text with "inner double quotes"'` — used when text contains `"`
3. **Backtick templates**: `` `text with newlines\n and 'both' "quotes"` `` — used for multi-line content or when text has both quote types

### Element patterns

- **Paragraph**: `(0,J.jsx)(V.p,{children:"text"})` or `children:'...'` (single-quote) or `(0,J.jsxs)(V.p,{children:[...]})`
- **Heading** (simple): `(0,J.jsx)(V.h2,{id:"slug",children:"text"})`
- **Heading** (compound): `(0,J.jsxs)(V.h2,{id:"slug",children:[...]})` — parsed via `_parse_jsxs_children`
- **Strong/Em/Code/Link**: `(0,J.jsx)(V.strong,{children:"text"})` inside children arrays
- **Image**: `(0,J.jsx)(figureVar,{children:(0,J.jsx)(imgVar,{src:varName,alt:"..."})})` — image URLs stored in JS variables
- **List**: `(0,J.jsxs)(V.ul,{children:[...V.li...]})` — items may have backtick separators inside
- **Simple code block**: `V.pre,{children:...V.code,{className:"language-X",children:"plaintext"}}` — className is optional; children may be backtick template
- **Highlighted code block** (ch3+): `V.pre > V.code{className:"hljs language-java",children:[span+literal mix]}` — see "hljs code extraction" below
- **Table**: `"div"{className:"table-wrap"} > V.table > V.thead/V.tbody > V.tr > V.th/V.td`
- **Info box**: `"div"{className:"info-box",children:[icon, V.p]}` — icon alt determines Tip/Note/Warning/Info
- **Sample dialogue**: `"div"{className:"sample-dialogue",children:[V.p, br, V.p, ...]}` — speaker lines
- **Blockquote** (simple): `(0,J.jsx)(V.blockquote,{children:"text"})`
- **Blockquote** (compound): `(0,J.jsxs)(V.blockquote,{children:[...]})`
- **Backtick paragraph**: `(0,J.jsx)(V.p,{children:\`...\`})` — multi-line or mixed-quote text
- **Captioned figure**: `(0,J.jsx)(VAR,{caption:"...",children:(0,J.jsx)(VAR,{src:VAR,alt:"..."})})` — with `[\w$]+` for `$`-named JS vars
- **Center-wrapped figure**: `(0,J.jsx)("center",{children:...figure...})` — centered image
- **Center-wrapped table**: `(0,J.jsx)("center",{children:...table...})` — delegates to table parser
- **KaTeX math (inline)**: `(0,J.jsxs)(V.span,{className:"katex",...})` — rendered as `$...$`
- **KaTeX math (display)**: `className:"katex-display"` — rendered as `$$...$$`
- **Note block**: `"div"{className:"note-block"}` — rendered as info-box with type "Note"
- **Inside-out table**: `className:"inside-out"` — side-by-side code comparisons with raw string tags
- **Raw-string table**: `"table"/"thead"/"tbody"/"tr"/"td"` — ML course variant without namespace refs
- **Table caption**: `className:"tableCaption"` — italic paragraph
- **Footnotes**: `className:"footnotes",data-footnotes` — silently skipped
- **Chapter number**: Injected by `parse_content()` — large green number before title
- **Wrapper components**: Single-letter JSX wrappers unwrapped and children parsed inline
- **Bare namespace-ref table**: `(0,J.jsxs)(V.table,{children:[...]})` — table without `table-wrap` div wrapper
- **`mdx-table-wrap`/`code-table` variants**: Additional className values for table-wrap divs
- **`class` attribute note-block**: `"div"{class:"note-block"}` — Mobile System Design variant using `class` instead of `className`
- **Bare span wrapper**: `(0,J.jsx)("span",{children:...})` — unwrapped, children parsed inline
- **Info admonition**: Icon `alt:"Info"` accepted as fourth admonition type alongside Tip/Note/Warning

### hljs code extraction

The syntax-highlighted code blocks have children as an array of `V.span` JSX calls interleaved with string/backtick literals. The `_extract_hljs_code_text` function:

1. Finds all `V.span` JSX calls and computes their **exact ranges** using paren-depth tracking (must skip past the first `(0,J.jsx)` group to track the second `(V.span,{...})` group)
2. Extracts span text from `children:` values only (using lookbehind to skip `className:`)
3. Handles three children formats: `children:"..."`, `children:'...'`, `` children:`...` ``
4. Recursively handles compound spans with `children:[...]`
5. Collects standalone literals NOT inside any span range, skipping JSX property values
6. Merges everything sorted by position

### Element splitting

Top-level elements are separated by `` ,`\n` ``. The splitter tracks parenthesis and bracket depth. At depth 0, backticks trigger splits. At depth > 0, backticks are consumed as template literal content to prevent depth corruption.

### Unrecognized elements

When `_parse_single_element` cannot match an element, it prints a warning with the first 120 chars. This is critical for detecting new patterns in untested chapters. A warning like:

```
Warning: unrecognized element: (0,C.jsx)(e.p,{children:'Abstraction can simplify...
```

means a single-quoted paragraph pattern needs handling (already added).

## Data Model

```python
@dataclass
class ContentBlock:
    tag: str            # "heading", "p", "img", "ul", "ol", "pre",
                        # "blockquote", "hr", "info-box", "sample-dialogue",
                        # "table", "chapter-number"
    text: str           # content text (markdown formatting preserved)
    level: int          # heading level 1-6
    src: str            # absolute image URL
    alt: str            # image alt text
    children: list      # list items (list[str]), dialogue lines (list[str]), or table rows (list[list[str]])
    language: str       # code block language
    admonition_type: str  # "Tip", "Note", "Warning" for info-box
    headers: list       # table header cells
```

## CLI Options

| Flag                 | Description                                         |
| -------------------- | --------------------------------------------------- |
| `url`                | ByteByteGo course page URL (required)               |
| `--output-dir`, `-o` | Output directory (default: `.`)                     |
| `--cookies`, `-c`    | Path to Netscape cookie file (default: auto-detect) |
| `--dump-json`        | Also save raw `pageProps` JSON alongside MD/PDF     |
| `--all`              | Extract ALL chapters of the course via TOC             |

## Known Limitations

- **Token expiry**: Firebase JWT expires hourly. Re-export cookies from browser when paid chapters fail.
- **Image alt text**: ByteByteGo generates very long AI-written alt text. Could truncate.
- **beautifulsoup4**: Only used for legacy `_table_html_to_md` fallback. Could be removed.
- **No test suite**: Verification is manual. Use `--dump-json` to save fixtures and compare output.
- **Silent image failures**: Failed image fetches (network errors, invalid SVG) are logged as warnings and skipped in the PDF. No placeholder is inserted.

## Test Fixtures (`responses/`)

Pre-captured JSON from live pages. **Use these instead of hitting the network** when:

- Cookies are expired or unavailable (paid chapters won't return data without a valid token)
- Debugging or modifying the parser offline
- Verifying that code changes don't break existing chapter parsing
- Examining raw JSX structure for new element types

| File                                    | What it is                               | When to use                                                         |
| --------------------------------------- | ---------------------------------------- | ------------------------------------------------------------------- |
| `responses/nextdata/ch01.json`          | Ch1 pageProps (free, 11KB code)          | Baseline: simple content — paragraphs, headings, images only        |
| `responses/nextdata/ch02.json`          | Ch2 pageProps (free, 35KB code)          | Tests: info-box, sample-dialogue, lists, single-quoted strings      |
| `responses/nextdata/ch03.json`          | Ch3 pageProps (paid, 85KB code)          | Tests: hljs code blocks, tables, single-quoted paragraphs           |
| `responses/paywalled/ch03_no_auth.json` | Ch3 without cookies — empty `{}`         | Shows what a subagent sees when cookies are expired                 |
| `responses/toc.json`                    | Full 14-chapter TOC with free/paid flags | Use to plan batch processing or check which chapters are accessible |

**To parse a fixture directly** (no network needed):

```python
import json
from fetcher import _parse_mdx_code

with open("responses/nextdata/ch03.json") as f:
    props = json.load(f)
blocks = _parse_mdx_code(props["code"])
# Now inspect blocks without fetching from bytebytego.com
```

**To add new fixtures**: run `python __main__.py <url> --dump-json -o responses/nextdata/` which saves the raw `pageProps` alongside MD/PDF.

## How to Debug New Chapters

1. Run with `--dump-json` to save the raw pageProps
2. Check the console for `Warning: unrecognized element:` messages
3. Compare `Content blocks: N` against the expected count
4. Open the JSON fixture and search for the unrecognized pattern
5. Add a new handler in `_parse_single_element` in `fetcher.py`
6. See [Reference.md](Reference.md) for raw JSX samples of every known pattern

## Chapters Captured

All 186 chapters across all 6 courses have been captured. See the "Current Progress" section for details.

## Additional Courses to Extract

### Course 2: Coding Patterns (120 chapters)

URL base: `https://bytebytego.com/courses/coding-patterns/`

**URL format**: `https://bytebytego.com/courses/coding-patterns/{section-slug}/{lesson-slug}`

Note: This course has a different URL structure from OOD — it has TWO path segments after the course name (section/lesson) instead of one.

Example: `https://bytebytego.com/courses/coding-patterns/two-pointers/introduction-to-two-pointers`

Free: Ch01-00 through Ch01-04 (5 chapters). All others PAID.

| Ch    | Section               | Chapters | Lessons (00-based) |
| ----- | --------------------- | -------- | ------------------ |
| Ch01  | Two Pointers          | 7        | 00: Introduction to Two Pointers, 01: Pair Sum - Sorted, 02: Triplet Sum, 03: Is Palindrome Valid, 04: Largest Container, 05: Move Zeros, 06: Dutch National Flag |
| Ch02  | Hash Maps and Sets    | 6        | 00-05 |
| Ch03  | Linked Lists          | 7        | 00-06 |
| Ch04  | Fast and Slow Pointers| 4        | 00-03 |
| Ch05  | Sliding Windows       | 4        | 00-03 |
| Ch06  | Binary Search         | 9        | 00-08 |
| Ch07  | Stacks                | 7        | 00-06 |
| Ch08  | Heaps                 | 5        | 00-04 |
| Ch09  | Intervals             | 4        | 00-03 |
| Ch10  | Prefix Sums           | 4        | 00-03 |
| Ch11  | Trees                 | 13       | 00-12 |
| Ch12  | Tries                 | 4        | 00-03 |
| Ch13  | Graphs                | 11       | 00-10 |
| Ch14  | Backtracking          | 6        | 00-05 |
| Ch15  | Dynamic Programming   | 10       | 00-09 |
| Ch16  | Greedy                | 4        | 00-03 |
| Ch17  | Sort and Search       | 5        | 00-04 |
| Ch18  | Bit Manipulation      | 4        | 00-03 |
| Ch19  | Math and Geometry     | 6        | 00-05 |

### Course 3: Tech Resume (19 chapters)

URL base: `https://bytebytego.com/courses/tech-resume/`

Free: 4 chapters (Ch2-5). 15 PAID.

| Ch | Title                                                                              | Access | Slug                                                              |
| -- | ---------------------------------------------------------------------------------- | ------ | ----------------------------------------------------------------- |
| 1  | Acknowledgements                                                                   | PAID   | p0-c1-acknowledgements                                            |
| 2  | Introduction                                                                       | FREE   | p0-c2-introduction                                                |
| 3  | PART 1: RESUMES AND THE HIRING PROCESS                                             | FREE   | p1-c0-resumes-and-the-hiring-process                              |
| 4  | Chapter 1: Why Resumes and CVs are Important                                       | FREE   | p1-c1-why-resumes-and-cvs-are-important                           |
| 5  | Chapter 2: The Hiring Pipeline                                                     | FREE   | p1-c2-the-hiring-pipeline                                         |
| 6  | PART 2: WRITING THE RESUME                                                         | PAID   | p2-c0-writing-the-resume                                          |
| 7  | Chapter 3: Tech Resume Basics                                                      | PAID   | p2-c1-tech-resume-basics                                          |
| 8  | Chapter 4: Resume Structure                                                        | PAID   | p2-c2-resume-structure                                            |
| 9  | Chapter 5: Standing Out                                                            | PAID   | p2-c3-standing-out                                                |
| 10 | Chapter 6: Common Mistakes                                                         | PAID   | p2-c4-common-mistakes                                             |
| 11 | Chapter 7: Different Experience Levels, Different Career Paths                     | PAID   | p2-c5-different-experience-levels-different-career-paths          |
| 12 | Chapter 8: Exercises to Polish Your Resume                                         | PAID   | p2-c6-exercises-to-polish-your-resume                             |
| 13 | Chapter 9: Beyond the Resume                                                       | PAID   | p2-c7-beyond-the-resume                                           |
| 14 | PART 3: EXAMPLES AND INSPIRATION                                                   | PAID   | p3-c0-examples-and-inspiration                                    |
| 15 | Chapter 10: Good Resume Template Principles                                        | PAID   | p3-c1-good-resume-template-principles                             |
| 16 | Chapter 11: Resume Templates                                                       | PAID   | p3-c2-resume-templates                                            |
| 17 | Chapter 12: Resume Improvement Examples                                            | PAID   | p3-c3-resume-improvement-examples                                 |
| 18 | Chapter 13: Advice for Hiring Managers on Running a Good Screening Process         | PAID   | p3-c4-advice-for-hiring-managers-on-running-a-good-screening-process |
| 19 | Conclusion                                                                         | PAID   | p4-c0-conclusion                                                  |

### Course 4: ML System Design Interview (11 chapters)

URL base: `https://bytebytego.com/courses/machine-learning-system-design-interview/`

Free: 4 chapters (Ch2, 4, 6, 10). 7 PAID.

| Ch | Title                                          | Access | Slug                                        |
| -- | ---------------------------------------------- | ------ | ------------------------------------------- |
| 1  | Introduction and Overview                      | PAID   | introduction-and-overview                   |
| 2  | Visual Search System                           | FREE   | visual-search-system                        |
| 3  | Google Street View Blurring System             | PAID   | google-street-view-blurring-system          |
| 4  | YouTube Video Search                           | FREE   | youtube-video-search                        |
| 5  | Harmful Content Detection                      | PAID   | harmful-content-detection                   |
| 6  | Video Recommendation System                    | FREE   | video-recommendation-system                 |
| 7  | Event Recommendation System                    | PAID   | event-recommendation-system                 |
| 8  | Ad Click Prediction on Social Platforms        | PAID   | ad-click-prediction-on-social-platforms     |
| 9  | Similar Listings on Vacation Rental Platforms  | PAID   | similar-listings-on-vacation-rental-platforms |
| 10 | Personalized News Feed                         | FREE   | personalized-news-feed                      |
| 11 | People You May Know                            | PAID   | people-you-may-know                         |

### Course 5: Mobile System Design Interview (11 chapters)

URL base: `https://bytebytego.com/courses/mobile-system-design-interview/`

Free: 3 chapters (Ch1-3). 8 PAID.

| Ch | Title                                              | Access | Slug                                              |
| -- | -------------------------------------------------- | ------ | ------------------------------------------------- |
| 1  | Introduction                                       | FREE   | introduction                                      |
| 2  | A framework for Mobile SD interviews               | FREE   | a-framework-for-mobile-sd-interviews              |
| 3  | News feed app                                      | FREE   | news-feed-app                                     |
| 4  | Chat app                                           | PAID   | chat-app                                          |
| 5  | Stock trading app                                  | PAID   | stock-trading-app                                 |
| 6  | Pagination library                                 | PAID   | pagination-library                                |
| 7  | Hotel reservation app                              | PAID   | hotel-reservation-app                             |
| 8  | Google Drive app                                   | PAID   | google-drive-app                                  |
| 9  | YouTube app                                        | PAID   | youtube-app                                       |
| 10 | Mobile System Design Building Blocks               | PAID   | mobile-system-design-building-blocks              |
| 11 | Quick Reference Cheat Sheet for MSD Interview      | PAID   | quick-reference-cheat-sheet-for-msd-interview     |

### Course 6: GenAI System Design Interview (11 chapters)

URL base: `https://bytebytego.com/courses/genai-system-design-interview/`

Free: 2 chapters (Ch1-2). 9 PAID.

| Ch | Title                              | Access | Slug                               |
| -- | ---------------------------------- | ------ | ---------------------------------- |
| 1  | Introduction and Overview          | FREE   | introduction-and-overview          |
| 2  | Gmail Smart Compose                | FREE   | gmail-smart-compose                |
| 3  | Google Translate                   | PAID   | google-translate                   |
| 4  | ChatGPT: Personal Assistant Chatbot| PAID   | chatgpt-personal-assistant-chatbot |
| 5  | Image Captioning                   | PAID   | image-captioning                   |
| 6  | Retrieval-Augmented Generation     | PAID   | retrieval-augmented-generation     |
| 7  | Realistic Face Generation          | PAID   | realistic-face-generation          |
| 8  | High-Resolution Image Synthesis    | PAID   | high-resolution-image-synthesis    |
| 9  | Text-to-Image Generation           | PAID   | text-to-image-generation           |
| 10 | Personalized Headshot Generation   | PAID   | personalized-headshot-generation   |
| 11 | Text-to-Video Generation           | PAID   | text-to-video-generation           |

### All Courses Summary

| Course                           | Chapters | Free | Paid |
| -------------------------------- | -------- | ---- | ---- |
| Object-Oriented Design Interview | 14       | 2    | 12   |
| Coding Patterns                  | 120      | 5    | 115  |
| Tech Resume                      | 19       | 4    | 15   |
| ML System Design Interview       | 11       | 4    | 7    |
| Mobile System Design Interview   | 11       | 3    | 8    |
| GenAI System Design Interview    | 11       | 2    | 9    |
| **Total**                        | **186**  | **20** | **166** |

### Autonomous Capture Workflow

For each chapter URL above, follow this loop:

1. **Run the extractor:**

   ```bash
   cd S:\git\3-useful-tools\bytebytego-extract
   python __main__.py "<URL>" --output-dir output --dump-json
   ```

   For batch extraction of an entire course:
   ```bash
   python __main__.py "<any-chapter-URL>" --all --output-dir output --dump-json
   ```

2. **Check console output for:**
   - `Content blocks: N` — if 0, cookies are expired or chapter is paywalled. Stop and report.
   - `Warning: unrecognized element:` — a new JSX pattern was found. **Do not skip it.** Fix the parser first (step 3), then re-run.
   - Any Python exceptions — fix the script, then re-run.

3. **If unrecognized elements found:**
   - Open the dumped JSON fixture to examine the raw JSX pattern
   - Add a new handler in `_parse_single_element()` in `fetcher.py`
   - Update the markdown converter and PDF exporter if the new element needs special rendering
   - Re-run the chapter and verify the warning is gone
   - Update the "Element patterns" section in this CLAUDE.md and Reference.md with the new pattern

4. **Quality check the output:**
   - Read the generated `.md` file
   - Verify: code blocks have proper string quotes, tables render correctly, no duplicate text, no missing headings
   - If quality issues found, fix the parser and re-run

5. **Update the Course Coverage table** in this CLAUDE.md after each successful chapter.

6. **Move to next chapter** only after the current one passes all checks.

### What to Do When Cookies Expire

If `Content blocks: 0` or `Empty pageProps` error:

- The Firebase JWT token has expired (1-hour lifetime)
- **Stop processing and report to the user** — they need to re-export cookies from their browser
- Do NOT skip the chapter or continue to the next one

### Known Patterns That May Appear in New Chapters

These have NOT been seen yet but are plausible if new courses are added:

- **Tabbed content** (e.g., showing same code in Java/Python/C++) — likely a custom div with className
- **Accordion/collapsible sections** — possible custom div pattern
- **Multi-file code examples** — may have filename headers before code blocks

When encountering any of these, add the pattern to `_parse_single_element()`, update both converters, and document in Reference.md.

## Dependencies

```
requests>=2.31.0       # HTTP fetching
beautifulsoup4>=4.12.0 # Legacy table HTML fallback
reportlab>=4.0.0       # PDF generation
svglib>=1.5.0          # SVG → ReportLab Drawing conversion
```

## Current Progress (2026-04-07)

### All 186 Chapters Extracted

All 6 courses have been fully extracted with 0 warnings and 0 errors:

| Course                           | Chapters | Output Directory                                |
| -------------------------------- | -------- | ----------------------------------------------- |
| Object-Oriented Design Interview | 14/14    | `output/object-oriented-design-interview/`      |
| Coding Patterns                  | 120/120  | `output/coding-patterns/`                       |
| Tech Resume                      | 19/19    | `output/tech-resume/`                           |
| ML System Design Interview       | 11/11    | `output/machine-learning-system-design-interview/` |
| Mobile System Design Interview   | 11/11    | `output/mobile-system-design-interview/`        |
| GenAI System Design Interview    | 11/11    | `output/genai-system-design-interview/`         |

### Parser Fixes Applied

1. **`intint` artifact in code blocks**: Nested spans extracted twice. Fix: filter out spans whose range is inside another span's range in `_extract_hljs_code_text`.

2. **Comment-line merging in code blocks**: hljs merges `//` comments with following code in single spans. Fix: line-by-line post-processor that splits merged lines, preserves indentation.

3. **Stray text leak (`MotorcycleCarScooter`)**: Quoted strings inside backtick templates extracted twice. Fix: reordered `_parse_jsxs_children` to extract backtick templates BEFORE quoted strings.

4. **Backtick template paragraphs**: New pattern — `(0,J.jsx)(V.p,{children:\`...\`})`. Added handler.

5. **Captioned figures**: `(0,J.jsx)(VAR,{caption:...})`. Added handler. Fixed `\w+` → `[\w$]+` for JS `$` variable names.

6. **Over-escaped regex backslashes**: Single-quoted and backtick paragraph handlers had `\\\\` instead of `\\`.

7. **Compound `li` with backtick children**: Added `re.DOTALL` to compound `li` regex in `_parse_list_items`.

8. **Single-quoted `li` children**: Added handler for `(0,J.jsxs)(V.li,{children:'...'})`.

9. **Dialogue text duplication**: Single-quote regex matched apostrophes inside double-quoted strings. Fixed with greedy non-overlapping filter.

10. **Compound `li` truncation**: Lazy `(.*?)` stopped at first `]}` in nested JSX. Replaced with bracket-depth tracking walker.

11. **Anchor link `li` items**: `rstrip(')}')` greedily stripped inner anchor's closing chars. Replaced with precise `[:-2]` slice.

12. **Inside-out table**: Rewrote to find `"td"` calls directly using `_extract_brace_content`.

13. **KaTeX backtick annotations**: Added backtick template (with `re.DOTALL`) and single-quoted branches.

14. **Center+text+KaTeX**: KaTeX inside `"center"` divs with surrounding text. Added handler delegating to `_parse_jsxs_children`.

15. **PDF image height overflow**: Raster images only scaled by width. Added max-height constraint.

16. **`*r` in image alt text crash**: Italic markdown conversion produced malformed XML. Added `_escape_xml_plain()`.

### PDF Rendering Improvements

1. **Proportional image sizing**: `_proportional_pdf_width()` scales images relative to 940px web content width, capped at 95%. Never upscales.

2. **Image centering**: Both SVG and raster images centered with `hAlign='CENTER'`.

3. **Syntax-highlighted code blocks**: `_code_block_to_flowables()` renders per-line `Paragraph` with VS Code light theme colors (keywords blue, strings green, comments gray, types teal, numbers orange). Wrapped in gray-background `Table`.

4. **Code block page splitting**: One-row-per-line `Table` with `splitByRow=1` prevents `Flowable too large` crash.

5. **List indentation**: `leftIndent=0`, `bulletIndent=0` — bullets are inline text.

6. **Chapter number**: 48pt Helvetica-Bold in `#16a34a` green, rendered before title.

### Batch Mode

The `--all` flag enables extracting an entire course from any chapter URL:

```bash
python __main__.py "https://bytebytego.com/courses/object-oriented-design-interview/design-a-parking-lot" --all --output-dir output
```

This fetches the TOC from the seed URL, then iterates all chapters sequentially. Stops immediately on any error (expired cookies, unrecognized elements, exceptions).

Key functions:
- `extract_toc(html)` — extracts `pageProps.toc` from fetched HTML
- `build_chapter_url(base, toc_entry)` — handles both string and list slugs
- `derive_course_base_url(url)` — strips chapter path to get course base

Note: `extract_all()` does not accept a `cookies` parameter directly. It relies on the module-level `fetcher.COOKIE_PATH` being set by `main()` before invocation.

### Known Source Data Issues (NOT parser bugs)

- Occasional `// //` double-comment prefix on some lines (raw data contains this)
- Some inline comments placed on wrong declaration lines

### Remaining Work

- **Empty `h3` in OOD Ch13**: `(0,e.jsx)(t.h3,{id:""})` — cosmetic only, no content loss
- **beautifulsoup4**: Only used for legacy fallback. Could be removed.
- **No test suite**: Verification is manual. Use `--dump-json` to save fixtures and compare output.
