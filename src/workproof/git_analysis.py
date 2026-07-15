"""Git analysis: files changed, test files added/modified/deleted between SHAs.

Pure git-plumbing via subprocess — no GitPython, no pygit2. We need exactly
three pieces of information per PR for the receipt:

1. ``files_changed`` — every path touched between base..head
2. ``test_files_added`` — paths that exist at head but not base, AND match the
   test-file heuristic for their language
3. ``test_files_modified`` / ``test_files_deleted`` — same, for the other transitions

Test-file detection is delegated to :mod:`workproof.heuristics` so the
language-specific regexes live in one place.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from workproof.heuristics import is_test_file


class GitAnalysisError(Exception):
    """Raised when git analysis fails (not a repo, bad SHA, etc.)."""


def _git(repo: Path, *args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=True,
            timeout=30,
        )
        return out.stdout
    except subprocess.CalledProcessError as e:
        raise GitAnalysisError(
            f"git {' '.join(args)} failed: {e.stderr.strip() or e.stdout.strip()}"
        ) from e
    except (subprocess.SubprocessError, FileNotFoundError) as e:
        raise GitAnalysisError(f"git command failed: {e}") from e


def diff_name_status(repo: Path, base: str, head: str) -> list[tuple[str, str]]:
    """Return ``[(status, path), ...]`` for files changed between base..head.

    Status codes are the git raw letters: A(dded), M(odified), D(eleted),
    R(enamed), C(opied), T(ype change), U(nmerged). Renames are reported as
    ``R<score>``; we keep the raw letter.
    """
    out = _git(repo, "diff", "--name-status", f"{base}..{head}")
    pairs: list[tuple[str, str]] = []
    for line in out.splitlines():
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 2:
            continue
        status = parts[0]
        # For renames/copies, the path is the *new* path (last column)
        path = parts[-1]
        pairs.append((status[0], path))  # first letter only
    return pairs


def categorize_files(pairs: list[tuple[str, str]]) -> dict[str, list[str]]:
    """Split ``[(status, path)]`` into added/modified/deleted/renamed buckets.

    Returned dict has keys: ``added``, ``modified``, ``deleted``, ``renamed``,
    ``copied``, ``typechange``. Missing buckets are empty lists.
    """
    out: dict[str, list[str]] = {
        "added": [],
        "modified": [],
        "deleted": [],
        "renamed": [],
        "copied": [],
        "typechange": [],
    }
    for status, path in pairs:
        if status == "A":
            out["added"].append(path)
        elif status == "M":
            out["modified"].append(path)
        elif status == "D":
            out["deleted"].append(path)
        elif status == "R":
            out["renamed"].append(path)
        elif status == "C":
            out["copied"].append(path)
        elif status == "T":
            out["typechange"].append(path)
    return out


def test_file_changes(repo: Path, base: str, head: str) -> dict[str, list[str]]:
    """Return ``{added, modified, deleted}`` lists of *test* files only.

    A file is a test file iff :func:`workproof.heuristics.is_test_file` returns
    True for its path. We classify by the *head* state of the path: a file
    modified at head that was a test file at head is a modified test file.
    """
    pairs = diff_name_status(repo, base, head)
    cats = categorize_files(pairs)
    return {
        "added": [p for p in cats["added"] if is_test_file(p)],
        "modified": [p for p in cats["modified"] if is_test_file(p)],
        "deleted": [p for p in cats["deleted"] if is_test_file(p)],
    }


def all_files_changed(repo: Path, base: str, head: str) -> list[str]:
    """Return a deduplicated, sorted list of all paths touched base..head."""
    pairs = diff_name_status(repo, base, head)
    return sorted({p for _status, p in pairs})
