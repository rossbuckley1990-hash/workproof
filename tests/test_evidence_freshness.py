"""Evidence freshness tests — tree equality, not clean-tree.

Three required fixtures:
(a) natural workflow edit→test→commit VERIFIES
(b) ancestor sabotage still fails closed
(c) dirty-tree-then-different-commit fails closed

The old clean-tree check was wrong: it forced contributors to commit before
testing. Nobody works that way. The fix replaces it with tree equality: the
entry's recorded working-tree hash (git write-tree) must equal the subject
commit's tree (git rev-parse <sha>^{tree}). This allows the natural
edit→test→commit workflow while still catching evidence laundering.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from typer.testing import CliRunner

from workproof.cli import app
from workproof.verifier import EXIT_INCOMPLETE

runner = CliRunner()


def _setup_repo(repo: Path, home: Path, monkeypatch) -> None:
    """Common setup: redirect HOME, patch keyring paths, init git repo."""
    monkeypatch.setenv("HOME", str(home))
    import workproof.keyring as kr

    monkeypatch.setattr(kr, "DEFAULT_KEYRING_DIR", home / ".workproof")
    monkeypatch.setattr(kr, "PRIVATE_KEY_PATH", home / ".workproof" / "id_ed25519")
    monkeypatch.setattr(kr, "PUBLIC_KEY_PATH", home / ".workproof" / "id_ed25519.pub")
    monkeypatch.chdir(repo)

    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "t@t.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "t"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.filemode", "false"], cwd=repo, check=True)

    (repo / ".workproof.yml").write_text(
        'policy_version: "0.1"\nallowed_commands:\n  - pytest\n  - python -m pytest\n  - python3 -m pytest\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(".workproof/\n__pycache__/\n*.pyc\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=str(repo), capture_output=True, text=True, check=True, timeout=10
    ).stdout.strip()


class TestNaturalWorkflowVerifies:
    """(a) The natural edit→test→commit workflow must VERIFY.

    The contributor edits code, runs tests (dirty tree), then commits.
    The entry's tree_hash matches the commit's tree because git captures
    the working tree state at commit time. This is the workflow everyone
    actually uses.
    """

    def test_edit_test_commit_verifies(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _setup_repo(repo, home, monkeypatch)

        # Edit: fix a bug (working tree is now dirty)
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (repo / "test_calc.py").write_text(
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
        )

        # Test: run pytest against the dirty (uncommitted) tree
        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])

        # Commit: captures the same working tree state
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fix: add() now adds"], cwd=repo, check=True)
        head = _git(repo, "rev-parse", "HEAD")

        # Attest against the commit
        result = runner.invoke(
            app, ["attest", "--ai-level", "assisted", "--agent", "honest", "--emit", "pr-body"]
        )
        assert result.exit_code == 0, result.output

        # Extract and verify
        import re

        m = re.search(r"```workproof-receipt\n(.*?)\n```", result.output, re.DOTALL)
        receipt_json = m.group(1)
        tmp_receipt = repo / "tmp-receipt.json"
        tmp_receipt.write_text(receipt_json, encoding="utf-8")

        r = runner.invoke(
            app,
            ["verify", str(tmp_receipt), "--repo", str(repo), "--expected-head-sha", head],
        )
        assert r.exit_code == 0, f"Natural workflow should verify:\n{r.output}"
        assert "VERIFIED" in r.output
        assert "evidence_freshness" in r.output.lower() or "evidence fresh" in r.output.lower()


class TestAncestorSabotageFails:
    """(b) Ancestor sabotage: receipt on parent X, sabotaged code in child Y.

    The tree hashes differ (X's tree ≠ Y's tree), so evidence_freshness fails.
    """

    def test_sabotage_via_different_tree_fails(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _setup_repo(repo, home, monkeypatch)

        # Commit X: working code
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (repo / "test_calc.py").write_text(
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "X: working"], cwd=repo, check=True)

        # Run tests against X (records tree_hash of X's tree)
        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])

        # Attest against X (honest so far)
        result = runner.invoke(
            app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit", "pr-body"]
        )
        assert result.exit_code == 0, result.output

        # Now sabotage: commit Y with different code
        (repo / "calc.py").write_text(
            "def add(a, b):\n    return a - b  # SABOTAGED\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "Y: sabotaged"], cwd=repo, check=True)
        commit_y = _git(repo, "rev-parse", "HEAD")

        # The receipt is for X; verify against Y → must FAIL
        import re

        m = re.search(r"```workproof-receipt\n(.*?)\n```", result.output, re.DOTALL)
        receipt_json = m.group(1)
        tmp_receipt = repo / "tmp-receipt.json"
        tmp_receipt.write_text(receipt_json, encoding="utf-8")

        r = runner.invoke(
            app,
            ["verify", str(tmp_receipt), "--repo", str(repo), "--expected-head-sha", commit_y],
        )
        # head_sha check catches this first (X ≠ Y), but if we only had
        # evidence_freshness, tree hashes would also differ.
        assert r.exit_code == EXIT_INCOMPLETE, (
            f"SABOTAGE: receipt for X verified against sabotaged Y!\n{r.output}"
        )
        assert "INCOMPLETE" in r.output


class TestDirtyTreeThenDifferentCommitFails:
    """(c) Dirty-tree-then-different-commit: edit A → test → edit B → commit.

    The entry records tree_hash of edit A. The commit captures edit B.
    Tree hashes differ → evidence_freshness fails.
    """

    def test_dirty_then_different_commit_fails(self, tmp_path: Path, monkeypatch) -> None:
        home = tmp_path / "home"
        home.mkdir()
        repo = tmp_path / "repo"
        repo.mkdir()
        _setup_repo(repo, home, monkeypatch)

        # Edit A: add a feature
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b + 0\n", encoding="utf-8")
        (repo / "test_calc.py").write_text(
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
        )

        # Test: run against edit A (records tree_hash of edit A)
        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])

        # Edit B: change the code to something DIFFERENT from edit A
        (repo / "calc.py").write_text("def add(a, b):\n    return a - b\n", encoding="utf-8")

        # Commit: captures edit B (not edit A)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "different code"], cwd=repo, check=True)
        head = _git(repo, "rev-parse", "HEAD")

        # Attest against the commit (which has edit B's tree)
        result = runner.invoke(
            app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit", "pr-body"]
        )
        assert result.exit_code == 0, result.output

        # Extract and verify — evidence was on edit A, commit has edit B
        import re

        m = re.search(r"```workproof-receipt\n(.*?)\n```", result.output, re.DOTALL)
        receipt_json = m.group(1)
        tmp_receipt = repo / "tmp-receipt.json"
        tmp_receipt.write_text(receipt_json, encoding="utf-8")

        r = runner.invoke(
            app,
            ["verify", str(tmp_receipt), "--repo", str(repo), "--expected-head-sha", head],
        )
        # The head_sha matches (we attested against HEAD), but evidence_freshness
        # catches the tree mismatch: entry's tree (edit A) ≠ commit's tree (edit B).
        assert r.exit_code == EXIT_INCOMPLETE, (
            f"DIRTY-THEN-DIFFERENT: evidence on edit A verified against edit B!\n{r.output}"
        )
        assert "INCOMPLETE" in r.output
        assert "tree" in r.output.lower() or "laundering" in r.output.lower()
