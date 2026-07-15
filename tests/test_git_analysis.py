"""Tests for git_analysis: diff name-status, file categorization, test-file detection."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from workproof.git_analysis import (
    all_files_changed,
    analyze_test_file_changes,
    categorize_files,
    diff_name_status,
)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        timeout=10,
    ).stdout.strip()


def _init_repo(repo: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.filemode", "false")


def _commit(repo: Path, files: dict[str, str], msg: str, delete: list[str] | None = None) -> str:
    """Commit ``files`` (path→content). Paths in ``delete`` are removed first."""
    for path in delete or []:
        full = repo / path
        if full.exists():
            full.unlink()
    for path, content in files.items():
        full = repo / path
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(content, encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)
    return _git(repo, "rev-parse", "HEAD")


@pytest.fixture
def repo_with_changes(tmp_path: Path) -> tuple[Path, str, str]:
    """Repo with: 1 added test, 1 modified test, 1 deleted test, 1 added source, 1 modified source."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _init_repo(repo)
    base = _commit(
        repo,
        {
            "test_keep.py": "def test_a():\n    assert 1\n",
            "test_delete.py": "def test_b():\n    assert 1\n",
            "app.py": "x = 1\n",
        },
        "base",
    )
    head = _commit(
        repo,
        {
            "test_keep.py": "def test_a():\n    assert 2\n",  # modified
            "test_new.py": "def test_c():\n    assert 1\n",  # added
            "app.py": "x = 2\n",  # modified source
            "new_module.py": "y = 1\n",  # added source
        },
        "head",
        delete=["test_delete.py"],  # deleted test
    )
    return repo, base, head


class TestDiffNameStatus:
    def test_returns_status_path_pairs(self, repo_with_changes) -> None:
        repo, base, head = repo_with_changes
        pairs = diff_name_status(repo, base, head)
        statuses = {p for s, p in pairs}
        assert "test_keep.py" in statuses
        assert "test_new.py" in statuses
        assert "test_delete.py" in statuses
        assert "app.py" in statuses
        assert "new_module.py" in statuses

    def test_correct_statuses(self, repo_with_changes) -> None:
        repo, base, head = repo_with_changes
        pairs = diff_name_status(repo, base, head)
        status_by_path = {p: s for s, p in pairs}
        assert status_by_path["test_new.py"] == "A"
        assert status_by_path["test_keep.py"] == "M"
        assert status_by_path["test_delete.py"] == "D"
        assert status_by_path["new_module.py"] == "A"
        assert status_by_path["app.py"] == "M"


class TestCategorizeFiles:
    def test_buckets(self) -> None:
        pairs = [
            ("A", "new.py"),
            ("M", "mod.py"),
            ("D", "del.py"),
            ("R", "renamed.py"),
            ("C", "copied.py"),
            ("T", "typechange.py"),
        ]
        cats = categorize_files(pairs)
        assert cats["added"] == ["new.py"]
        assert cats["modified"] == ["mod.py"]
        assert cats["deleted"] == ["del.py"]
        assert cats["renamed"] == ["renamed.py"]
        assert cats["copied"] == ["copied.py"]
        assert cats["typechange"] == ["typechange.py"]


class TestTestFileChanges:
    def test_filters_to_test_files_only(self, repo_with_changes) -> None:
        repo, base, head = repo_with_changes
        changes = analyze_test_file_changes(repo, base, head)
        assert "test_new.py" in changes["added"]
        assert "test_keep.py" in changes["modified"]
        assert "test_delete.py" in changes["deleted"]
        # Source files are excluded
        assert "app.py" not in changes["modified"]
        assert "new_module.py" not in changes["added"]


class TestAllFilesChanged:
    def test_returns_deduped_sorted(self, repo_with_changes) -> None:
        repo, base, head = repo_with_changes
        files = all_files_changed(repo, base, head)
        assert files == sorted(files)
        assert len(files) == len(set(files))
        assert set(files) == {
            "test_keep.py",
            "test_new.py",
            "test_delete.py",
            "app.py",
            "new_module.py",
        }
