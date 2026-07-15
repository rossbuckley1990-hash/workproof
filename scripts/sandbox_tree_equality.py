#!/usr/bin/env python3
"""Sandbox proof: tree-equality check works for both honest and sabotage flows.

Tests:
1. Honest: edit → test (records tree hash) → commit → verify. Tree hashes match. PASSES.
2. Sabotage: test on X → commit Y with different code → verify. Tree hashes differ. FAILS.
3. Dirty-then-different: edit A → test → edit B → commit → verify. Tree hashes differ. FAILS.
"""
import hashlib
import os
import subprocess
import tempfile
from pathlib import Path


def git(repo, *args, env=None):
    e = {**os.environ, **(env or {})}
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, env=e, timeout=10
    ).stdout.strip()


def working_tree_hash(repo):
    """git write-tree via a TEMP index — never mutates the user's real index."""
    git_dir = repo / ".git"
    tmp_index = str(git_dir / "workproof-tmp-index")
    env = {"GIT_INDEX_FILE": tmp_index}
    try:
        git(repo, "read-tree", "HEAD", env=env)  # load HEAD as base
        git(repo, "add", "-A", env=env)           # stage working tree changes
        return git(repo, "write-tree", env=env)   # write the tree
    finally:
        try:
            os.unlink(tmp_index)
        except OSError:
            pass


def commit_tree_hash(repo, sha):
    """git rev-parse <sha>^{tree}"""
    return git(repo, "rev-parse", f"{sha}^{{tree}}")


def init_repo(path):
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q"], cwd=str(path), check=True)
    git(path, "config", "user.email", "t@t.com")
    git(path, "config", "user.name", "t")
    git(path, "config", "commit.gpgsign", "false")
    (path / ".gitignore").write_text(".workproof/\n")
    return path


def test_natural_workflow_verifies():
    """edit → test → commit. Tree hashes match. Should VERIFY."""
    tmp = Path(tempfile.mkdtemp())
    repo = init_repo(tmp / "repo")
    # Base commit
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # buggy
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    # Edit (fix the bug) — working tree now differs from HEAD
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")  # fixed
    # Test (record tree hash of the dirty working tree)
    run_tree = working_tree_hash(repo)
    # Commit (captures the same working tree state)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "fix")
    head = git(repo, "rev-parse", "HEAD")
    commit_tree = commit_tree_hash(repo, head)
    # Check: run_tree == commit_tree
    assert run_tree == commit_tree, f"HONEST FAILED: {run_tree} != {commit_tree}"
    print(f"  ✓ natural workflow: run_tree={run_tree[:12]} == commit_tree={commit_tree[:12]}")


def test_sabotage_fails():
    """test on X → commit Y with different code. Tree hashes differ. Should FAIL."""
    tmp = Path(tempfile.mkdtemp())
    repo = init_repo(tmp / "repo")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")  # working
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "X: working")
    # Test on X (clean tree)
    run_tree = working_tree_hash(repo)
    # Sabotage: change the code, commit Y
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")  # sabotaged
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "Y: sabotaged")
    head = git(repo, "rev-parse", "HEAD")
    commit_tree = commit_tree_hash(repo, head)
    # Check: run_tree != commit_tree
    assert run_tree != commit_tree, f"SABOTAGE FAILED: trees match ({run_tree})"
    print(f"  ✓ sabotage detected: run_tree={run_tree[:12]} != commit_tree={commit_tree[:12]}")


def test_dirty_then_different_commit_fails():
    """edit A → test → edit B → commit. Tree hashes differ. Should FAIL."""
    tmp = Path(tempfile.mkdtemp())
    repo = init_repo(tmp / "repo")
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n")
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "base")
    # Edit A
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n")
    # Test (records tree hash of edit A)
    run_tree = working_tree_hash(repo)
    # Edit B (different from A)
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n")
    # Commit (captures edit B, not edit A)
    git(repo, "add", "-A")
    git(repo, "commit", "-q", "-m", "different")
    head = git(repo, "rev-parse", "HEAD")
    commit_tree = commit_tree_hash(repo, head)
    # Check: run_tree != commit_tree
    assert run_tree != commit_tree, f"DIRTY-DIFFERENT FAILED: trees match ({run_tree})"
    print(f"  ✓ dirty-then-different: run_tree={run_tree[:12]} != commit_tree={commit_tree[:12]}")


if __name__ == "__main__":
    print("Sandbox proof — tree equality check:")
    test_natural_workflow_verifies()
    test_sabotage_fails()
    test_dirty_then_different_commit_fails()
    print("\nAll three proven. The fix works.")
