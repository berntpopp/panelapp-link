"""Enforce source module line-count budgets for agent-friendly refactors."""

from __future__ import annotations

from pathlib import Path

MAX_SOURCE_LINES = 600
ROOT = Path(__file__).resolve().parents[1]
CHECK_PATHS = [
    ROOT / "panelapp_link",
    ROOT / "server.py",
    ROOT / "mcp_server.py",
]
ALLOWLIST_PATH = ROOT / ".loc-allowlist"


def read_allowlist() -> dict[Path, int]:
    """Read optional grandfathered file ceilings."""
    if not ALLOWLIST_PATH.exists():
        return {}
    allowlist: dict[Path, int] = {}
    for line in ALLOWLIST_PATH.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        path_text, _, ceiling_text = stripped.partition(":")
        allowlist[ROOT / path_text] = int(ceiling_text)
    return allowlist


def iter_python_files() -> list[Path]:
    """Return checked Python source files."""
    files: list[Path] = []
    for path in CHECK_PATHS:
        if not path.exists():
            continue
        if path.is_file():
            files.append(path)
        else:
            files.extend(sorted(path.rglob("*.py")))
    return files


def line_count(path: Path) -> int:
    """Count physical lines in a source file."""
    return len(path.read_text().splitlines())


def main() -> int:
    """Check source files and print violations."""
    allowlist = read_allowlist()
    failures: list[str] = []
    for path in iter_python_files():
        count = line_count(path)
        ceiling = allowlist.get(path, MAX_SOURCE_LINES)
        if count > ceiling:
            rel = path.relative_to(ROOT)
            failures.append(f"{rel}: {count} lines exceeds limit {ceiling}")

    if failures:
        print("File-size budget exceeded:")
        for failure in failures:
            print(f"  {failure}")
        return 1

    print(f"File-size budget OK: {MAX_SOURCE_LINES} line default")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
