#!/usr/bin/env python3
# PURPOSE: Computes and applies the next version number for `make release`,
# rewriting the __version__ constant in bin/quarry. Kept out of src/ because it
# is a release-time tool, not part of the shipped command.

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

BUMPS = ("major", "minor", "patch")

# The one line that carries the version, e.g. __version__ = "0.1.0"
PATTERN = re.compile(r'^__version__ = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)


def next_version(current: str, bump: str) -> str:
    """The version that `bump` moves `current` to, resetting lesser fields."""
    if bump not in BUMPS:
        raise ValueError(f"unknown bump '{bump}' (expected one of {', '.join(BUMPS)})")
    fields = current.split(".")
    if len(fields) != 3 or not all(f.isdigit() for f in fields):
        raise ValueError(f"unparseable version '{current}' (expected MAJOR.MINOR.PATCH)")
    major, minor, patch = (int(f) for f in fields)
    if bump == "major":
        return f"{major + 1}.0.0"
    if bump == "minor":
        return f"{major}.{minor + 1}.0"
    return f"{major}.{minor}.{patch + 1}"


def read_version(path: Path) -> str:
    match = PATTERN.search(Path(path).read_text(encoding="utf-8"))
    if not match:
        raise ValueError(f"no __version__ constant found in {path}")
    return ".".join(match.groups())


def write_version(path: Path, version: str) -> None:
    path = Path(path)
    text = path.read_text(encoding="utf-8")
    updated, count = PATTERN.subn(f'__version__ = "{version}"', text, count=1)
    if count != 1:
        raise ValueError(f"no __version__ constant found in {path}")
    path.write_text(updated, encoding="utf-8")


def main(argv: list[str]) -> int:
    p = argparse.ArgumentParser(
        description="Print or apply the next version number.",
    )
    p.add_argument("bump", choices=BUMPS)
    p.add_argument("--file", required=True, type=Path,
                   help="file holding the __version__ constant")
    p.add_argument("--print", dest="print_only", action="store_true",
                   help="print the next version without rewriting the file")
    args = p.parse_args(argv)

    try:
        version = next_version(read_version(args.file), args.bump)
        if not args.print_only:
            write_version(args.file, version)
    except (ValueError, OSError) as exc:
        print(f"bump_version: {exc}", file=sys.stderr)
        return 1

    print(version)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
