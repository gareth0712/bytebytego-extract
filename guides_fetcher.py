"""Fetch guide data from ByteByteGo's public GitHub repo (system-design-101).

No authentication required — all guide data is publicly available on GitHub.
"""

import re
import time
from pathlib import Path

import requests

_RATE_LIMIT_SLEEP_CAP = 300  # seconds — max sleep when rate-limited by GitHub

GITHUB_TREE_API = (
    "https://api.github.com/repos/ByteByteGoHq/system-design-101/git/trees/main?recursive=1"
)
RAW_BASE = "https://raw.githubusercontent.com/ByteByteGoHq/system-design-101/main/"

_HEADERS = {
    "User-Agent": "bytebytego-extract/1.0 (https://github.com/local/bytebytego-extract)",
    "Accept": "application/vnd.github.v3+json",
}


def _check_github_rate_limit(resp: requests.Response, attempt: int) -> bool:
    """Return True if the response is a GitHub rate-limit 403 and we should retry.

    On the first attempt, logs a warning, sleeps until the reset time (capped at
    ``_RATE_LIMIT_SLEEP_CAP`` seconds), and returns True (caller should retry).
    On the second attempt, raises RuntimeError with a clear message.
    """
    if resp.status_code != 403:
        return False
    remaining = resp.headers.get("X-RateLimit-Remaining", "")
    if remaining != "0":
        return False

    reset_epoch_str = resp.headers.get("X-RateLimit-Reset", "")
    try:
        reset_epoch = int(reset_epoch_str)
        sleep_secs = min(max(reset_epoch - int(time.time()), 0), _RATE_LIMIT_SLEEP_CAP)
    except (ValueError, TypeError):
        sleep_secs = _RATE_LIMIT_SLEEP_CAP

    if attempt == 0:
        print(
            f"  [warn] GitHub rate limit hit. "
            f"Sleeping {sleep_secs}s until reset..."
        )
        time.sleep(sleep_secs)
        return True

    raise RuntimeError(
        "GitHub API rate limit still exceeded after waiting. "
        "Set a GITHUB_TOKEN environment variable (or add 'Authorization: token <PAT>' "
        "to _HEADERS) to raise the limit from 60 to 5000 requests/hour."
    )


def list_guide_entries() -> list[dict]:
    """Return all guide entries from the GitHub tree API.

    Each dict has keys: ``slug`` (str), ``path`` (str), ``sha`` (str).
    The slug is the path relative to ``data/guides/`` with the ``.md`` extension removed.
    Detects GitHub rate-limit 403s and sleeps + retries once before raising.
    """
    for attempt in range(2):
        resp = requests.get(GITHUB_TREE_API, headers=_HEADERS, timeout=30)
        if _check_github_rate_limit(resp, attempt):
            continue  # slept; retry
        resp.raise_for_status()
        break

    tree: list[dict] = resp.json().get("tree", [])

    entries: list[dict] = []
    for node in tree:
        path: str = node.get("path", "")
        if path.startswith("data/guides/") and path.endswith(".md"):
            # slug is everything after "data/guides/" minus ".md"
            slug = path[len("data/guides/"):-len(".md")]
            entries.append({"slug": slug, "path": path, "sha": node.get("sha", "")})
    return entries


def fetch_guide_markdown(slug: str) -> str:
    """Fetch the raw markdown content for a guide by its slug."""
    url = f"{RAW_BASE}data/guides/{slug}.md"
    resp = requests.get(url, headers=_HEADERS, timeout=30)
    resp.raise_for_status()
    return resp.text


def _parse_yaml_value(raw: str) -> str | list[str] | bool | None:
    """Parse a single YAML value string (scalar, inline list, or bool)."""
    raw = raw.strip()
    if raw.startswith("[") and raw.endswith("]"):
        # Inline list: [a, b, c] — strip brackets and split
        inner = raw[1:-1]
        items = [item.strip().strip("'\"") for item in inner.split(",") if item.strip()]
        return items
    if raw.lower() == "true":
        return True
    if raw.lower() == "false":
        return False
    # Strip surrounding quotes if present
    if (raw.startswith('"') and raw.endswith('"')) or (
        raw.startswith("'") and raw.endswith("'")
    ):
        return raw[1:-1]
    return raw


def parse_frontmatter(raw_md: str) -> tuple[dict, str]:
    """Split YAML frontmatter from the markdown body.

    Returns ``(metadata_dict, body_markdown)``.

    Handles both inline list ``[a, b]`` and block list (dash-prefixed) formats.
    Does NOT depend on PyYAML.
    """
    if not raw_md.startswith("---"):
        return {}, raw_md

    # Find the closing ---
    rest = raw_md[3:]
    end_idx = rest.find("\n---")
    if end_idx == -1:
        return {}, raw_md

    frontmatter_text = rest[:end_idx].strip()
    body = rest[end_idx + 4:].lstrip("\n")  # skip past "\n---"

    meta: dict = {}
    current_key: str | None = None
    current_list: list[str] | None = None

    for line in frontmatter_text.splitlines():
        # Detect a new key: "key: value" or "key:" (start of block list)
        kv_match = re.match(r'^(\w[\w-]*):\s*(.*)', line)
        if kv_match:
            # Flush any pending list
            if current_key is not None and current_list is not None:
                meta[current_key] = current_list
                current_list = None
                current_key = None

            key = kv_match.group(1)
            value_str = kv_match.group(2).strip()

            if value_str == "":
                # Block list will follow (dash-prefixed lines)
                current_key = key
                current_list = []
            else:
                parsed = _parse_yaml_value(value_str)
                meta[key] = parsed
        elif line.startswith("  - ") or line.startswith("- "):
            # Block list item
            item = line.lstrip(" ").lstrip("- ").strip().strip("'\"")
            if current_list is not None:
                current_list.append(item)

    # Flush final pending list
    if current_key is not None and current_list is not None:
        meta[current_key] = current_list

    return meta, body


def sanitize_slug_for_filesystem(slug: str) -> str:
    """Make a guide slug safe for Windows filenames.

    Rules:
    - Remove apostrophes (``'``)
    - Replace ``+`` with ``plus``
    - Replace other filesystem-unsafe chars with ``-``
    """
    result = slug.replace("'", "").replace("+", "plus")
    # Replace Windows-forbidden characters (<>:"/\\|?*) and control chars
    result = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "-", result)
    # Collapse consecutive hyphens
    result = re.sub(r"-{2,}", "-", result)
    return result.strip("-")


def fetch_image(url: str, dest_path: Path) -> bool:
    """Download an image from ``url`` to ``dest_path``.

    Retries once on transient (5xx or network-level) failures.
    Returns True on success, False on failure.
    4xx errors are NOT retried — they indicate a permanent client/server mismatch.
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(2):
        try:
            resp = requests.get(url, headers=_HEADERS, timeout=30, stream=True)
            if resp.status_code >= 500 and attempt == 0:
                print(f"  [warn] Image fetch got {resp.status_code} (attempt 1), retrying...")
                time.sleep(2)
                continue
            # 4xx errors: raise immediately without retry
            resp.raise_for_status()
            with open(dest_path, "wb") as fh:
                for chunk in resp.iter_content(chunk_size=8192):
                    fh.write(chunk)
            return True
        except (requests.ConnectionError, requests.Timeout, requests.ChunkedEncodingError) as exc:
            # Network-level errors: retry once
            if attempt == 0:
                print(f"  [warn] Image fetch failed (attempt 1), retrying: {exc}")
                time.sleep(2)
            else:
                print(f"  [warn] Image fetch failed (attempt 2), skipping: {exc}")
        except requests.HTTPError as exc:
            # 4xx or unhandled 5xx (after retry exhausted): do not retry
            print(f"  [warn] Image fetch HTTP error, skipping: {exc}")
            return False
    return False
