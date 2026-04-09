# Reference: ByteByteGo MDX Internals

Detailed research notes on how the compiled MDX content is structured, with raw JSX samples for every element type. Use this when debugging the parser or adding support for new element types.

## How Content Reaches the Browser

ByteByteGo is a Next.js app using MDX (Markdown + JSX) for course content. At build time:

1. MDX source files are compiled into JavaScript modules
2. Each chapter becomes a self-executing JS function that returns JSX
3. This compiled JS is embedded as a string in `pageProps.code` inside the `__NEXT_DATA__` JSON blob
4. The browser executes this JS client-side to render the React components

We never see the original MDX source. We parse the compiled JS output.

## The __NEXT_DATA__ Blob

Every ByteByteGo page contains this in its HTML:
```html
<script id="__NEXT_DATA__" type="application/json">{"props":{"pageProps":{...}}}</script>
```

### pageProps fields
| Field | Type | Description |
|-------|------|-------------|
| `course` | string | Course slug, e.g. `"object-oriented-design-interview"` |
| `id` | string | Chapter slug |
| `chapter` | int | Chapter number (1-indexed) |
| `title` | string | Human-readable title |
| `free` | bool | If false, `code` is empty without auth cookies |
| `code` | string | The compiled MDX JS module |
| `toc` | array | All chapters: `[{title, slug, chapter, free}, ...]` |
| `courseMetadata` | object | Course-level info: title, authors, lastModified |

## Compiled MDX Structure

The `code` field contains a self-executing JS module:
```javascript
var Component=(()=>{
  var p=Object.create; ...
  // Variable assignments for images:
  var l="/images/courses/.../image-1-1-HASH.svg";
  var d="/images/courses/.../image-1-2-HASH.svg";
  // ...
  // The content function:
  function M(t={}){
    let {wrapper:C}=t.components||{};
    return (0,e.jsx)(e.Fragment,{children:[
      // ALL CONTENT IS HERE as JSX calls
    ]})
  }
  return Component;
})();
```

### Variable Name Instability (CRITICAL)

The compiled MDX uses minified variable names that change between chapters:

| Chapter | JSX Caller | Component Namespace | Example |
|---------|-----------|-------------------|---------|
| Ch1 | `e` | `C` | `(0,e.jsx)(C.p,{...})` |
| Ch2 | `e` | `i` | `(0,e.jsx)(i.p,{...})` |
| Ch3 | `C` | `e` | `(0,C.jsx)(e.p,{...})` |
| Ch4 | `C` | `e` | `(0,C.jsx)(e.p,{...})` |

The parser uses `\w+` for both positions.

### Image Variable Assignments

Image URLs are stored as JS variables at the top of the module:
```javascript
var l="/images/courses/object-oriented-design-interview/what-is-an-object-oriented-design-interview/image-1-1-AFIBSFEU.svg"
var d="/images/courses/object-oriented-design-interview/what-is-an-object-oriented-design-interview/image-1-2-4XTE4MPH.svg"
```

In JSX calls, `src:l` references the variable `l`. The parser maps these with `img_vars`.

## Element Patterns (Raw JSX Samples)

### Paragraph (simple)
```
(0,e.jsx)(C.p,{children:"Object-oriented design interviews have become..."})
```

### Paragraph (compound — mixed inline elements)
```
(0,e.jsxs)(C.p,{children:[
  (0,e.jsx)(C.strong,{children:"GitHub repo link"}),
  ": ",
  (0,e.jsx)(C.a,{href:"http://github.com/...",children:"github.com/..."})
]})
```

### Heading
```
(0,e.jsx)(C.h2,{id:"how-is-this-book-structured",children:"How Is This Book Structured?"})
```
Supports h1 through h6.

### Image (in Figure wrapper)
```
(0,e.jsx)(figureVar,{children:(0,e.jsx)(imgVar,{src:l,alt:"Image represents...",width:"624",height:"409"})})
```
- `src:l` references a JS variable (not a string literal)
- alt text is often very long (AI-generated)

### Image (with caption wrapper)
```
(0,e.jsx)(n,{caption:"OOD Interview Framework",children:(0,e.jsx)(C,{src:c,alt:"Image represents..."})})
```

### Unordered List
```
(0,e.jsxs)(i.ul,{children:[
  `\n`,
  (0,e.jsxs)(i.li,{children:[
    (0,e.jsx)(i.strong,{children:"Top-down Approach"}),
    ": First, identify high-level components..."
  ]}),
  `\n`,
  (0,e.jsxs)(i.li,{children:[
    (0,e.jsx)(i.strong,{children:"Bottom-up Approach"}),
    ": Define concrete classes first..."
  ]}),
  `\n`
]})
```
Note the backtick `\n` separators between list items inside the bracket-delimited array.

### Simple Code Block (plain string children)
```
(0,e.jsx)(C.pre,{children:(0,e.jsx)(C.code,{className:"language-java",children:"public class Foo {}"})})
```
Seen in chapters 1-2. Children is a single string.

### Syntax-Highlighted Code Block (hljs — array children)
```
(0,C.jsx)(e.pre,{children:(0,C.jsxs)(e.code,{className:"hljs language-java",children:[
  (0,C.jsx)(e.span,{className:"hljs-keyword",children:"public"}),
  " ",
  (0,C.jsx)(e.span,{className:"hljs-keyword",children:"class"}),
  " ",
  (0,C.jsx)(e.span,{className:"hljs-title class_",children:"Person"}),
  ` {\n    `,
  (0,C.jsx)(e.span,{className:"hljs-comment",children:"// Private data members"}),
  `\n    `,
  (0,C.jsx)(e.span,{className:"hljs-keyword",children:"private"}),
  ` String name;\n    `,
  ...
]})})
```

Key details:
- `className` has `hljs ` prefix: `"hljs language-java"`
- `children` is an **array** (note `jsxs` not `jsx`)
- Array contains a mix of `e.span` calls and string/backtick literals
- Span classNames: `hljs-keyword`, `hljs-comment`, `hljs-type`, `hljs-title class_`, `hljs-title function_`, `hljs-params`, `hljs-built_in`, `hljs-string`, `hljs-number`, `hljs-meta`, `hljs-operator`, `hljs-variable`, `hljs-literal`
- Compound spans can nest: `(0,C.jsxs)(e.span,{className:"hljs-params",children:["(String name, ",(0,C.jsx)(e.span,{className:"hljs-type",children:"int"})," age)"]})`

The parser strips all span wrappers and concatenates text content. When extracting from spans, use `(?<=children:)"..."` to avoid capturing `className:"hljs-keyword"` as text.

### Table
```
(0,C.jsx)("div",{className:"table-wrap",
  style:{"--table-min-width":"640px"},
  children:(0,C.jsxs)(e.table,{children:[
    (0,C.jsx)(e.thead,{children:(0,C.jsxs)(e.tr,{children:[
      (0,C.jsx)(e.th,{children:"Characteristics"}),
      (0,C.jsx)(e.th,{children:"Abstraction"}),
      (0,C.jsx)(e.th,{children:"Encapsulation"})
    ]})}),
    (0,C.jsxs)(e.tbody,{children:[
      (0,C.jsxs)(e.tr,{children:[
        (0,C.jsx)(e.td,{children:(0,C.jsx)(e.strong,{children:"Focus"})}),
        (0,C.jsx)(e.td,{children:"Hiding complexity..."}),
        (0,C.jsx)(e.td,{children:"Bundling data and methods..."})
      ]}),
      ... more rows ...
    ]})
  ]})
})
```

Wrapped in a `"div"` with `className:"table-wrap"`. Table cells (`th`/`td`) can contain simple strings or nested JSX (e.g., `e.strong`).

### Info Box
```
(0,e.jsxs)("div",{className:"info-box",children:[
  (0,e.jsx)(C,{src:d,alt:"Tip",width:"21",height:"21"}),
  (0,e.jsxs)(i.p,{children:[
    (0,e.jsx)(i.strong,{children:"Tip:"}),
    ` In an OOD interview, the journey is just as important...`
  ]})
]})
```
Icon image determines admonition type (`"Tip"`, `"Note"`, `"Warning"`).

### Sample Dialogue
```
(0,e.jsxs)("div",{className:"sample-dialogue",children:[
  (0,e.jsxs)(i.p,{children:[
    (0,e.jsx)(i.strong,{children:"Anne:"}),
    ` What types of vehicles should the parking lot support?`
  ]}),
  (0,e.jsx)("br",{}),
  (0,e.jsxs)(i.p,{children:[
    (0,e.jsx)(i.strong,{children:"Beth:"}),
    " Yes, and also buses. Each bus takes up three spots."
  ]}),
  (0,e.jsx)("br",{}),
  ...
]})
```
Speaker lines separated by `<br>` elements.

## Element Splitting Deep Dive

The Fragment's children array uses `` ,`\n` `` as separators between top-level elements:
```
(0,e.jsx)(C.p,{children:"First paragraph"}),`
`,(0,e.jsx)(C.p,{children:"Second paragraph"})
```

BUT the same backtick pattern appears INSIDE nested structures:
```
(0,e.jsxs)(i.ul,{children:[`
`,(0,e.jsx)(i.li,{children:"Item 1"}),`
`,(0,e.jsx)(i.li,{children:"Item 2"}),`
`]})
```

And inside hljs code block children arrays:
```
(0,C.jsxs)(e.code,{className:"hljs language-java",children:[
  (0,C.jsx)(e.span,{className:"hljs-keyword",children:"public"}),
  ` class Foo {\n    `,
  ...
]})
```

The splitter handles this by:
1. Tracking `depth` (parentheses) and `bracket_depth` (square brackets)
2. At depth 0: backtick triggers element split
3. At depth > 0: backtick is consumed as template literal content (the character-by-character loop reads until the next backtick)
4. Double-quoted strings are consumed whole to avoid false depth changes from `{` or `[` inside strings

## Authentication

ByteByteGo uses Firebase Auth. The `token` cookie is a Firebase JWT. For paywalled chapters:
- Without a valid `token` cookie, `pageProps` is empty `{}`
- With a valid cookie, full `pageProps` including `code` is returned
- The token has a short expiry (1 hour). The cookie file at `S:\program-files\cookies\cookies-bytebytego.txt` must be refreshed by re-exporting from browser after login.
- The `csrf-token` cookie is also present but not required for GET requests.

## Bugs Fixed During Development

1. **Hardcoded `C` as component namespace** — Ch2 uses `i`, causing zero matches. Fix: `\w+`
2. **Hardcoded `e` as JSX caller** — Ch3 uses `C`, causing zero matches. Fix: `\w+`
3. **Naive backtick splitting** — First version used `re.split(r',`\s*`')` which split inside lists. Fix: depth-aware character-by-character splitter
4. **Nested backtick corruption** — Backtick template literals inside code arrays contain `{` and `[` that corrupted depth counters. Fix: consume nested backticks as opaque content
5. **`className` leaking into code text** — `className:"hljs-keyword"` matched the same regex as `children:"text"`. Fix: use `(?<=children:)` lookbehind and skip strings preceded by known property names
6. **SVG height overflow** — Some SVGs are taller than a page. Fix: cap height to `A4[1] - 80mm`
7. **Duplicate text in compound paragraphs** — `_parse_jsxs_children` captured both the JSX span text and its raw string literal. Fix: track JSX span ranges and skip plain strings inside them
