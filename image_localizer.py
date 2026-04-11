"""Download remote images referenced in ContentBlocks to a local directory.

After localization, img blocks have their ``src`` rewritten to a relative
path like ``{chapter_stem}_images/image-1-1-AFIBSFEU.svg`` that is valid
from the sibling .md/.pdf files.  Non-img blocks pass through unchanged.
Failed downloads log a warning and leave ``src`` as the original URL so
the PDF exporter can fall back to HTTP fetching.
"""

import hashlib
import re
from dataclasses import replace
from pathlib import Path
from urllib.parse import urlparse

import requests

from fetcher import ContentBlock

# Characters forbidden in Windows filenames (beyond the ones already stripped
# by urllib parsing).  We replace them with underscores.
_UNSAFE_CHARS_RE = re.compile(r"[\"'<>|:*?+#\x00-\x1f]")

_DOWNLOAD_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/146.0.0.0 Safari/537.36"
    )
}


def _safe_image_filename(url: str, index: int, seen: dict[str, int]) -> str:
    """Derive a filesystem-safe filename from an image URL.

    Steps:
    1. Strip query string and fragment from the URL path.
    2. Take the last path segment as the base filename.
    3. Sanitize Windows-unsafe characters.
    4. If the result is empty, fall back to a hash-derived name.
    5. Disambiguate duplicate basenames by appending ``_{n}`` before the
       extension (``_2``, ``_3``, …).

    Args:
        url: Absolute image URL.
        index: Position of this image in the chapter (0-based), used only
               as a fallback when the URL has no meaningful filename.
        seen: Mutable dict mapping sanitized basenames (without
              disambiguation suffix) to a running count.  Pass the *same*
              dict for all images in one chapter so duplicates are detected.

    Returns:
        A sanitized filename string (no directory component).
    """
    parsed = urlparse(url)
    raw_name = parsed.path.rstrip("/").split("/")[-1]  # e.g. "image-1-1-AFIBSFEU.svg"

    # Sanitize forbidden characters
    safe = _UNSAFE_CHARS_RE.sub("_", raw_name)
    safe = safe.strip("._")  # avoid leading dots/underscores

    if not safe:
        # Fallback: short sha256 of the full URL
        url_hash = hashlib.sha256(url.encode()).hexdigest()[:12]
        safe = f"img_{index:04d}_{url_hash}"

    # Split into stem + suffix for disambiguation
    dot_idx = safe.rfind(".")
    if dot_idx > 0:
        stem, ext = safe[:dot_idx], safe[dot_idx:]
    else:
        stem, ext = safe, ""

    count = seen.get(stem, 0) + 1
    seen[stem] = count
    if count == 1:
        return f"{stem}{ext}"
    return f"{stem}_{count}{ext}"


def _download_image(
    url: str,
    dest_path: Path,
    session: requests.Session,
) -> bool:
    """Download ``url`` to ``dest_path``.

    Returns True on success, False on failure (error already printed).
    """
    dest_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        resp = session.get(url, headers=_DOWNLOAD_HEADERS, timeout=20)
        resp.raise_for_status()
        dest_path.write_bytes(resp.content)
        return True
    except requests.RequestException as exc:
        print(f"  [warn] Could not download image {url}: {exc}")
        return False


def localize_images(
    blocks: list[ContentBlock],
    images_dir: Path,
    session: requests.Session | None = None,
) -> list[ContentBlock]:
    """Download every img block's src to ``images_dir`` and rewrite src to a
    relative path suitable for both the Markdown file and the PDF exporter.

    Non-img blocks are returned unchanged.  Failed downloads log a warning
    and leave the block's src as the original URL so the PDF exporter can
    fall back to HTTP fetching.

    Args:
        blocks: Parsed content blocks from ``parse_content()``.
        images_dir: Destination directory for downloaded images.  Created
                    automatically if it does not exist.
        session: Optional ``requests.Session`` for connection pooling.  A
                 new session is created internally if not provided.

    Returns:
        A new list of ContentBlocks with img blocks' src rewritten to
        relative paths (e.g. ``{chapter_stem}_images/filename.svg``).
        All other blocks are returned by reference (no copy needed since
        only img blocks are touched).
    """
    own_session = session is None
    if own_session:
        session = requests.Session()

    try:
        seen: dict[str, int] = {}
        result: list[ContentBlock] = []
        img_index = 0

        for block in blocks:
            if block.tag != "img" or not block.src.startswith("http"):
                # Pass non-img blocks and already-local paths through unchanged
                result.append(block)
                continue

            filename = _safe_image_filename(block.src, img_index, seen)
            img_index += 1
            dest_path = images_dir / filename

            if dest_path.exists():
                # Already downloaded (e.g. re-running the extractor)
                print(f"  [cache] {filename}")
            else:
                success = _download_image(block.src, dest_path, session)
                if not success:
                    # Leave src as original URL — PDF exporter will try HTTP
                    result.append(block)
                    continue
                print(f"  [img]   {filename}")

            # Rewrite src to a path relative to the output directory
            # (images_dir is expected to be a sibling of the .md/.pdf files,
            #  so the relative reference is just "{images_dir.name}/{filename}")
            relative_src = f"{images_dir.name}/{filename}"
            result.append(replace(block, src=relative_src))

        return result
    finally:
        if own_session:
            session.close()
