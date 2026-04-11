"""Interactive entrypoint for the ByteByteGo extractor.

Prompts the user to choose between Course and Guide extraction,
then delegates to the appropriate module.

Usage:
    python run.py
"""

import sys

from guides_main import ALL_CATEGORIES


def _prompt(prompt: str, valid: set[str]) -> str:
    """Prompt until a valid choice is entered."""
    while True:
        answer = input(prompt).strip()
        if answer in valid:
            return answer
        print(f"  Please enter one of: {', '.join(sorted(valid))}")


def main() -> None:
    print("=" * 60)
    print("ByteByteGo Extractor")
    print("=" * 60)
    print()
    print("Which extractor?")
    print("  (1) Courses  — extract a course chapter (requires auth cookies)")
    print("  (2) Guides   — extract engineering visual guides (no auth needed)")
    print()

    choice = _prompt("Enter 1 or 2: ", {"1", "2"})

    if choice == "1":
        _run_courses()
    else:
        _run_guides()


def _run_courses() -> None:
    """Collect course parameters and delegate to __main__.main()."""
    print()
    url = input("Enter the ByteByteGo course page URL: ").strip()
    if not url:
        print("No URL provided. Exiting.")
        sys.exit(1)

    output_dir = input("Output directory [default: output]: ").strip() or "output"
    cookies = input("Path to cookie file [default: auto-detect]: ").strip() or None
    dump_json = input("Save raw JSON? (y/N): ").strip().lower() == "y"
    extract_all = input("Extract ALL chapters? (y/N): ").strip().lower() == "y"

    # Build sys.argv and delegate
    args = [url, "--output-dir", output_dir]
    if cookies:
        args += ["--cookies", cookies]
    if dump_json:
        args.append("--dump-json")
    if extract_all:
        args.append("--all")

    # Patch sys.argv and call main()
    sys.argv = ["__main__.py"] + args

    from __main__ import main as courses_main
    courses_main()


def _run_guides() -> None:
    """Collect guide parameters and delegate to guides_main.main()."""
    print()
    print("Which category?")
    for idx, cat in enumerate(ALL_CATEGORIES, start=1):
        print(f"  ({idx:>2}) {cat}")
    print(f"  ({len(ALL_CATEGORIES) + 1:>2}) ALL categories")
    print()

    valid_choices = {str(i) for i in range(1, len(ALL_CATEGORIES) + 2)}
    choice = _prompt(f"Enter 1-{len(ALL_CATEGORIES) + 1}: ", valid_choices)

    output_dir = (
        input("Output directory [default: output/engineering-visual-guides]: ").strip()
        or "output/engineering-visual-guides"
    )

    idx = int(choice)
    if idx == len(ALL_CATEGORIES) + 1:
        sys.argv = ["guides_main.py", "--all", "--output-dir", output_dir]
    else:
        category = ALL_CATEGORIES[idx - 1]
        sys.argv = ["guides_main.py", "--category", category, "--output-dir", output_dir]

    from guides_main import main as guides_main_fn
    guides_main_fn()


if __name__ == "__main__":
    main()
