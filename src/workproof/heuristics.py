"""Test-weakening heuristics: counts, never judgment.

Per D08 in DECISIONS.md, this module reports *counts* of patterns that
*could* indicate test weakening — assertions removed, new skip/xfail markers
added. It never labels a PR "suspicious". The reviewer reads the table;
Workproof refuses to be the jury.

Per the style guide, every heuristic ships:
- one true-positive fixture test
- one known-false-positive fixture test (with the limitation documented)

Supported languages and their patterns (see ``LANGUAGE_PATTERNS``):
- Python (pytest): ``assert`` statements, ``@pytest.mark.skip`` / ``xfail``
- JavaScript/TypeScript (jest/vitest): ``expect(...).`` calls, ``it.skip`` /
  ``describe.skip`` / ``test.skip``
- Go (testing): ``t.Skip`` / ``t.Skipf``, removed ``if err != nil { t.Fatal }``
  is intentionally NOT detected (too noisy)
"""

from __future__ import annotations

import re
import subprocess
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

# ----- language detection -----

LANGUAGE_PATTERNS: dict[str, dict[str, list[str]]] = {
    "python": {
        "extensions": [".py"],
        "assertion_patterns": [r"^\s*assert\s"],
        "skip_patterns": [
            r"^\s*@\s*pytest\.mark\.skip",
            r"^\s*@\s*pytest\.mark\.xfail",
            r"^\s*pytest\.skip\(",
            r"^\s*pytest\.xfail\(",
        ],
    },
    "javascript": {
        "extensions": [".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"],
        "assertion_patterns": [r"\bexpect\s*\("],
        "skip_patterns": [
            r"\b(?:it|test|describe)\.skip\s*\(",
            r"\bxit\s*\(",
            r"\bxdescribe\s*\(",
        ],
    },
    "go": {
        "extensions": [".go"],
        # Go has no single "assert" keyword; t.Fatal / t.Errorf are calls, not assertions
        "assertion_patterns": [],
        "skip_patterns": [r"\bt\.Skip(?:f)?\s*\("],
    },
}


def detect_language(path: str) -> str | None:
    """Return the language key for ``path`` based on extension, or ``None``."""
    p = Path(path)
    for lang, spec in LANGUAGE_PATTERNS.items():
        if p.suffix in spec["extensions"]:
            return lang
    return None


def is_test_file(path: str) -> bool:
    """Heuristic: is ``path`` a test file in its language's convention?

    Conservative on purpose — we'd rather miss a test file than flag a
    production file as a test file.

    - Python: basename starts with ``test_`` or ends with ``_test.py``
    - JS/TS: basename contains ``.test.`` or ``.spec.`` or starts with ``test``
    - Go: basename ends with ``_test.go`` (Go convention is enforced by the compiler)
    """
    p = Path(path)
    name = p.name
    if p.suffix == ".py":
        return name.startswith("test_") or name.endswith("_test.py")
    if p.suffix in (".js", ".jsx", ".ts", ".tsx", ".mjs", ".cjs"):
        return ".test." in name or ".spec." in name or name.startswith("test.")
    if p.suffix == ".go":
        return name.endswith("_test.go")
    return False


# ----- diff analysis -----


@dataclass
class HeuristicResult:
    """Counts of weakening signals with file:line anchors for each occurrence."""

    assertions_removed: int = 0
    assertions_removed_details: list[dict[str, str | int]] = field(default_factory=list)
    new_skip_markers: int = 0
    new_skip_markers_details: list[dict[str, str | int]] = field(default_factory=list)


def _git_diff_for_file(repo: Path, base: str, head: str, path: str) -> str:
    """Return the unified diff for ``path`` between base..head, or '' if no diff."""
    try:
        out = subprocess.run(
            ["git", "diff", base, head, "--", path],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return out.stdout
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _count_removed_lines_matching(diff: str, patterns: list[str]) -> list[tuple[int, str]]:
    """Find removed lines (starting with ``-``) matching any of ``patterns``.

    Returns ``[(line_number_in_diff, line_content), ...]``. The line number is
    1-indexed within the diff hunk (not the original file) — sufficient for
    anchor reporting.
    """
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(diff.splitlines(), start=1):
        if not line.startswith("-") or line.startswith("---"):
            continue
        content = line[1:]
        for pat in patterns:
            if re.search(pat, content, re.MULTILINE):
                out.append((lineno, content.strip()))
                break
    return out


def _count_added_lines_matching(diff: str, patterns: list[str]) -> list[tuple[int, str]]:
    """Find added lines (starting with ``+``) matching any of ``patterns``."""
    out: list[tuple[int, str]] = []
    for lineno, line in enumerate(diff.splitlines(), start=1):
        if not line.startswith("+") or line.startswith("+++"):
            continue
        content = line[1:]
        for pat in patterns:
            if re.search(pat, content, re.MULTILINE):
                out.append((lineno, content.strip()))
                break
    return out


def analyze_diff(repo: Path, base: str, head: str, paths: list[str]) -> HeuristicResult:
    """Run heuristics across ``paths`` and return aggregated counts + anchors.

    Only files whose language has patterns are inspected; others are skipped
    silently. Removed assertions and newly-added skip markers are counted
    independently per file.
    """
    result = HeuristicResult()
    for path in paths:
        lang = detect_language(path)
        if lang is None:
            continue
        spec = LANGUAGE_PATTERNS[lang]
        diff = _git_diff_for_file(repo, base, head, path)
        if not diff:
            continue

        for lineno, content in _count_removed_lines_matching(diff, spec["assertion_patterns"]):
            result.assertions_removed += 1
            result.assertions_removed_details.append(
                {"file": path, "line": lineno, "content": content}
            )
        for lineno, content in _count_added_lines_matching(diff, spec["skip_patterns"]):
            result.new_skip_markers += 1
            result.new_skip_markers_details.append(
                {"file": path, "line": lineno, "content": content}
            )
    return result


def summarize(result: HeuristicResult) -> dict[str, int]:
    """Return a flat ``{assertions_removed, new_skip_markers}`` count dict."""
    return {
        "assertions_removed": result.assertions_removed,
        "new_skip_markers": result.new_skip_markers,
    }


def counts_by_file(details: list[dict[str, str | int]]) -> dict[str, int]:
    """Aggregate ``details`` into ``{path: count}``. Useful for the Markdown table."""
    return dict(Counter(str(d["file"]) for d in details))
