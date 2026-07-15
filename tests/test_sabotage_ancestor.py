"""Sabotage-via-ancestor fixture test.

The attack: receipt on parent commit X (where tests pass), sabotaged code in
child commit Y (where tests would fail), verify against Y. Without
--allow-ancestor (now deleted), this MUST fail because receipt's head_sha (X)
≠ expected (Y). Under every emit mode (pr-body, notes, file), the result is
INCOMPLETE — never VERIFIED.

This test exists because --allow-ancestor was removed for re-opening this
exact hole. If anyone re-adds ancestor logic, this test will catch it.
"""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from workproof.cli import app
from workproof.verifier import EXIT_INCOMPLETE

runner = CliRunner()


@pytest.fixture
def sabotage_repo(tmp_path, monkeypatch):
    """Repo with: commit X (tests pass), commit Y (sabotaged code on top).

    The contributor records evidence on X, attests against X, then adds
    sabotaged code as Y and tries to pass off the X-receipt as evidence for Y.
    """
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
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

    # Base commit
    (repo / ".workproof.yml").write_text(
        'policy_version: "0.1"\nallowed_commands:\n  - pytest\n  - python -m pytest\n  - python3 -m pytest\n',
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)

    # Commit X: working code with passing test
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "X: working code"], cwd=repo, check=True)
    commit_x = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Init + run tests against X + attest against X (honest so far)
    # The attestation happens BEFORE Y is committed, so head_sha = X.
    runner.invoke(app, ["init"])
    runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])
    # Capture the receipt for X in all three emit modes (before Y is committed)
    receipt_pr_body = runner.invoke(
        app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit", "pr-body"]
    )
    assert receipt_pr_body.exit_code == 0, receipt_pr_body.output
    # Also emit to notes and file while HEAD is still X
    receipt_notes = runner.invoke(
        app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit", "notes"]
    )
    assert receipt_notes.exit_code == 0, receipt_notes.output
    receipt_file = runner.invoke(
        app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit", "file"]
    )
    assert receipt_file.exit_code == 0, receipt_file.output

    # Commit Y: sabotaged code on top of X
    (repo / "calc.py").write_text(
        "def add(a, b):\n    return a - b  # SABOTAGED\n", encoding="utf-8"
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "Y: sabotaged"], cwd=repo, check=True)
    commit_y = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    return repo, commit_x, commit_y, receipt_pr_body.output


class TestSabotageViaAncestor:
    """The receipt is for commit X. The attacker tries to pass it off as
    evidence for commit Y (which has sabotaged code). Every emit mode must
    fail because head_sha (X) ≠ expected (Y)."""

    def test_sabotage_fails_under_pr_body(self, sabotage_repo) -> None:
        """pr-body mode: receipt JSON is in the PR body. head_sha=X, expected=Y → INCOMPLETE."""
        repo, commit_x, commit_y, pr_body_output = sabotage_repo

        # Extract the receipt JSON from the output (between ```workproof-receipt and ```)
        import re

        m = re.search(r"```workproof-receipt\n(.*?)\n```", pr_body_output, re.DOTALL)
        assert m, "pr-body output must contain a ```workproof-receipt block"
        receipt_json = m.group(1)
        receipt_dict = json.loads(receipt_json)

        # The receipt's subject is X, not Y
        assert receipt_dict["statement"]["subject"][0]["digest"]["gitSha"] == commit_x

        # Verify against Y (the sabotaged commit) — must FAIL
        tmp_receipt = repo / "tmp-receipt.json"
        tmp_receipt.write_text(receipt_json, encoding="utf-8")
        r = runner.invoke(
            app,
            ["verify", str(tmp_receipt), "--repo", str(repo), "--expected-head-sha", commit_y],
        )
        assert r.exit_code == EXIT_INCOMPLETE, (
            f"SABOTAGE VIA ANCESTOR: receipt for X verified against sabotaged Y!\n{r.output}"
        )
        assert "INCOMPLETE" in r.output

    def test_sabotage_fails_under_notes(self, sabotage_repo) -> None:
        """notes mode: receipt is a git note on X. Verify against Y → INCOMPLETE."""
        repo, commit_x, commit_y, _ = sabotage_repo

        # The note was written on commit X (before Y was committed)
        note_content = subprocess.run(
            ["git", "notes", "--ref=workproof", "show", commit_x],
            cwd=repo,
            capture_output=True,
            text=True,
            check=True,
        ).stdout
        receipt_dict = json.loads(note_content)
        assert receipt_dict["statement"]["subject"][0]["digest"]["gitSha"] == commit_x

        tmp_receipt = repo / "tmp-receipt.json"
        tmp_receipt.write_text(note_content, encoding="utf-8")
        r = runner.invoke(
            app,
            ["verify", str(tmp_receipt), "--repo", str(repo), "--expected-head-sha", commit_y],
        )
        assert r.exit_code == EXIT_INCOMPLETE, (
            f"SABOTAGE VIA ANCESTOR (notes): receipt for X verified against sabotaged Y!\n{r.output}"
        )
        assert "INCOMPLETE" in r.output

    def test_sabotage_fails_under_file(self, sabotage_repo) -> None:
        """file mode: receipt written to .workproof/receipts/<X>.json. Verify against Y → INCOMPLETE."""
        repo, commit_x, commit_y, _ = sabotage_repo

        receipt_path = repo / ".workproof" / "receipts" / f"{commit_x}.json"
        assert receipt_path.exists()

        r = runner.invoke(
            app,
            ["verify", str(receipt_path), "--repo", str(repo), "--expected-head-sha", commit_y],
        )
        assert r.exit_code == EXIT_INCOMPLETE, (
            f"SABOTAGE VIA ANCESTOR (file): receipt for X verified against sabotaged Y!\n{r.output}"
        )
        assert "INCOMPLETE" in r.output

    def test_honest_receipt_verifies_under_pr_body(self, sabotage_repo) -> None:
        """Control: receipt for Y (the actual PR head) verifies against Y.

        This confirms the fix doesn't break the honest workflow: the contributor
        commits Y, records evidence against Y, attests against Y, pastes into PR body.
        """
        repo, _commit_x, _commit_y, _ = sabotage_repo

        # Fix the sabotage, commit Z, record evidence against Z, attest against Z
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "Z: fixed"], cwd=repo, check=True)
        commit_z = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # Reset session and record against Z (clean tree)
        # Remove session file and commit the removal so the tree is clean
        session_path = repo / ".workproof" / "session.jsonl"
        if session_path.exists():
            session_path.unlink()
        # Also remove any receipts from the X attestations
        receipts_dir = repo / ".workproof" / "receipts"
        if receipts_dir.exists():
            import shutil

            shutil.rmtree(receipts_dir)
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(
            ["git", "commit", "-q", "-m", "clean up workproof artifacts"], cwd=repo, check=True
        )
        # Re-derive HEAD (the cleanup commit is now the attest target)
        commit_z = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])

        result = runner.invoke(
            app, ["attest", "--ai-level", "assisted", "--agent", "honest", "--emit", "pr-body"]
        )
        assert result.exit_code == 0, result.output

        import re

        m = re.search(r"```workproof-receipt\n(.*?)\n```", result.output, re.DOTALL)
        receipt_json = m.group(1)
        tmp_receipt = repo / "tmp-receipt.json"
        tmp_receipt.write_text(receipt_json, encoding="utf-8")
        r = runner.invoke(
            app,
            ["verify", str(tmp_receipt), "--repo", str(repo), "--expected-head-sha", commit_z],
        )
        assert r.exit_code == 0, f"Honest receipt for Z should verify:\n{r.output}"
        assert "VERIFIED" in r.output
