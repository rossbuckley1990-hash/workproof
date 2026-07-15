"""End-to-end CLI tests via Typer's CliRunner.

These exercise the full init → run → attest → verify workflow in a temp git
repo with real commits. They are slower than unit tests but catch integration
bugs that pure-unit tests miss.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest
from typer.testing import CliRunner

from workproof.cli import app

runner = CliRunner()


@pytest.fixture
def isolated_home(tmp_path, monkeypatch):
    """Redirect $HOME and $PWD so keys and .workproof/ land in tmp_path."""
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: home))
    # Override keyring paths via env (keyring reads Path.home() at import time)
    # Easier: monkeypatch the module-level constants
    import workproof.keyring as kr

    monkeypatch.setattr(kr, "DEFAULT_KEYRING_DIR", home / ".workproof")
    monkeypatch.setattr(kr, "PRIVATE_KEY_PATH", home / ".workproof" / "id_ed25519")
    monkeypatch.setattr(kr, "PUBLIC_KEY_PATH", home / ".workproof" / "id_ed25519.pub")
    monkeypatch.chdir(repo)
    # Init a git repo with a base commit
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Test"], cwd=repo, check=True)
    (repo / "README.md").write_text("# test\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)
    yield repo


def _make_head_commit(repo: Path) -> str:
    """Add a second commit so base..head has a real diff. Returns head SHA."""
    (repo / "app.py").write_text("def f():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=repo, check=True)
    out = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    )
    return out.stdout.strip()


class TestInit:
    def test_init_creates_policy_and_keys(self, isolated_home) -> None:
        result = runner.invoke(app, ["init"])
        assert result.exit_code == 0, result.output
        assert (isolated_home / ".workproof.yml").exists()
        # Keys in $HOME/.workproof/
        home = Path(os.environ["HOME"])
        assert (home / ".workproof" / "id_ed25519").exists()
        assert (home / ".workproof" / "id_ed25519.pub").exists()
        # Private key perms
        mode = (home / ".workproof" / "id_ed25519").stat().st_mode & 0o777
        assert mode == 0o600

    def test_init_is_idempotent(self, isolated_home) -> None:
        r1 = runner.invoke(app, ["init"])
        assert r1.exit_code == 0
        first_pub = (Path(os.environ["HOME"]) / ".workproof" / "id_ed25519.pub").read_bytes()
        r2 = runner.invoke(app, ["init"])
        assert r2.exit_code == 0
        second_pub = (Path(os.environ["HOME"]) / ".workproof" / "id_ed25519.pub").read_bytes()
        # Without --force, keys are kept
        assert first_pub == second_pub

    def test_init_force_regenerates_keys(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        first = (Path(os.environ["HOME"]) / ".workproof" / "id_ed25519.pub").read_bytes()
        runner.invoke(app, ["init", "--force"])
        second = (Path(os.environ["HOME"]) / ".workproof" / "id_ed25519.pub").read_bytes()
        assert first != second


class TestRun:
    def test_run_records_exit_code(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        # Use python -c so the command is portable
        r = runner.invoke(app, ["run", "--", "python3", "-c", "print('hi')"])
        assert r.exit_code == 0
        session_path = isolated_home / ".workproof" / "session.jsonl"
        assert session_path.exists()
        entries = [
            json.loads(line) for line in session_path.read_text().splitlines() if line.strip()
        ]
        assert len(entries) == 1
        assert entries[0]["exit_code"] == 0
        assert entries[0]["argv"] == ["python3", "-c", "print('hi')"]

    def test_run_propagates_nonzero_exit(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["run", "--", "python3", "-c", "import sys; sys.exit(3)"])
        assert r.exit_code == 3
        session_path = isolated_home / ".workproof" / "session.jsonl"
        entries = [
            json.loads(line) for line in session_path.read_text().splitlines() if line.strip()
        ]
        assert entries[0]["exit_code"] == 3

    def test_run_chains_multiple_entries(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-c", "print(1)"])
        runner.invoke(app, ["run", "--", "python3", "-c", "print(2)"])
        session_path = isolated_home / ".workproof" / "session.jsonl"
        entries = [
            json.loads(line) for line in session_path.read_text().splitlines() if line.strip()
        ]
        assert len(entries) == 2
        # Genesis: prev_hash is null
        assert entries[0]["prev_hash"] is None
        # Second: prev_hash links to first's hash
        assert entries[1]["prev_hash"] == entries[0]["hash"]

    def test_run_writes_evidence_blobs(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-c", "print('output')"])
        evidence_dir = isolated_home / ".workproof" / "evidence"
        assert evidence_dir.exists()
        gz_files = list(evidence_dir.glob("*.out.gz"))
        assert gz_files, "stdout evidence blob not written"


class TestAttest:
    def test_attest_writes_receipt_and_prints_markdown(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        head_sha = _make_head_commit(isolated_home)
        runner.invoke(app, ["run", "--", "python3", "-c", "print('test')"])
        r = runner.invoke(
            app,
            ["attest", "--ai-level", "assisted", "--agent", "claude-code"],
        )
        assert r.exit_code == 0, r.output
        # Receipt file written
        receipt_path = isolated_home / ".workproof" / "receipts" / f"{head_sha}.json"
        assert receipt_path.exists()
        # Markdown in output
        assert "Workproof Receipt" in r.output
        assert "claude-code" in r.output
        assert "<!-- workproof:sticky:v1 -->" in r.output

    def test_attest_without_session_errors(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        # Make a second commit so base..head resolves, then explicitly pass --base
        # to avoid any HEAD~1 resolution ambiguity in the test environment.
        (isolated_home / "app.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=isolated_home, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "head"], cwd=isolated_home, check=True)
        base_sha = subprocess.run(
            ["git", "rev-parse", "HEAD~1"],
            cwd=isolated_home,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=isolated_home,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        r = runner.invoke(
            app,
            [
                "attest",
                "--ai-level",
                "assisted",
                "--agent",
                "x",
                "--base",
                base_sha,
                "--head",
                head_sha,
            ],
        )
        assert r.exit_code != 0
        assert "no session" in r.output.lower()


class TestStatus:
    def test_status_shows_state_after_init(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        r = runner.invoke(app, ["status"])
        assert r.exit_code == 0, r.output
        assert "keys present" in r.output
        assert "policy" in r.output.lower()
        assert "no session" in r.output.lower() or "session" in r.output.lower()

    def test_status_shows_session_after_run(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-c", "print('x')"])
        r = runner.invoke(app, ["status"])
        assert r.exit_code == 0, r.output
        assert "1 entry" in r.output or "1 entry/entries" in r.output

    def test_status_shows_receipts_after_attest(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        _make_head_commit(isolated_home)
        runner.invoke(app, ["run", "--", "python3", "-c", "print('x')"])
        runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "x"])
        r = runner.invoke(app, ["status"])
        assert r.exit_code == 0, r.output
        assert "1 receipt" in r.output or "receipts:" in r.output.lower()


class TestVerify:
    def test_verify_succeeds_on_fresh_receipt(self, isolated_home) -> None:
        runner.invoke(app, ["init"])
        head_sha = _make_head_commit(isolated_home)
        runner.invoke(app, ["run", "--", "python3", "-c", "print('test')"])
        runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "claude-code"])
        receipt_path = isolated_home / ".workproof" / "receipts" / f"{head_sha}.json"
        r = runner.invoke(app, ["verify", str(receipt_path), "--repo", str(isolated_home)])
        assert r.exit_code == 0, r.output
        assert "VERIFIED" in r.output

    def test_verify_catches_edited_receipt(self, isolated_home) -> None:
        """Acceptance criterion 3a: edited receipt is detected."""
        runner.invoke(app, ["init"])
        head_sha = _make_head_commit(isolated_home)
        runner.invoke(app, ["run", "--", "python3", "-c", "print('test')"])
        runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "claude-code"])
        receipt_path = isolated_home / ".workproof" / "receipts" / f"{head_sha}.json"
        # Edit the embedded statement (this breaks payload/statement match)
        d = json.loads(receipt_path.read_text())
        d["statement"]["predicate"]["ai_level"] = "none"
        receipt_path.write_text(json.dumps(d))
        r = runner.invoke(app, ["verify", str(receipt_path)])
        assert r.exit_code == 1, r.output
        assert "TAMPERED" in r.output

    def test_verify_catches_replay_against_different_sha(self, isolated_home) -> None:
        """Acceptance criterion 3b: receipt replayed against a different SHA."""
        runner.invoke(app, ["init"])
        head_sha = _make_head_commit(isolated_home)
        runner.invoke(app, ["run", "--", "python3", "-c", "print('test')"])
        runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "claude-code"])
        receipt_path = isolated_home / ".workproof" / "receipts" / f"{head_sha}.json"
        # Now create another commit so HEAD differs
        (isolated_home / "another.py").write_text("x = 1\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=isolated_home, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "another"], cwd=isolated_home, check=True)
        r = runner.invoke(app, ["verify", str(receipt_path), "--repo", str(isolated_home)])
        assert r.exit_code == 2, r.output
        assert "INCOMPLETE" in r.output

    def test_verify_catches_assertion_removal_via_heuristics_warn(self, isolated_home) -> None:
        """Acceptance criterion 3c: PR that removed an assertion is flagged (⚠ not ✗).

        Note: per the threat model, heuristics are *informational* — they
        don't change the exit code. They appear as a ⚠ row in the table.
        The receipt itself verifies; the reviewer sees the warning.
        """
        runner.invoke(app, ["init"])
        # Base commit with a test that has an assertion
        (isolated_home / "test_app.py").write_text(
            "def test_x():\n    assert 1 == 1\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=isolated_home, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base+test"], cwd=isolated_home, check=True)
        # Head commit that removes the assertion
        (isolated_home / "test_app.py").write_text("def test_x():\n    pass\n", encoding="utf-8")
        subprocess.run(["git", "add", "-A"], cwd=isolated_home, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "weaken"], cwd=isolated_home, check=True)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=isolated_home,
            capture_output=True,
            text=True,
            check=True,
        ).stdout.strip()
        runner.invoke(app, ["run", "--", "python3", "-c", "print('test')"])
        runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "claude-code"])
        receipt_path = isolated_home / ".workproof" / "receipts" / f"{head_sha}.json"
        r = runner.invoke(app, ["verify", str(receipt_path), "--repo", str(isolated_home)])
        # Exit 0 (heuristics are informational) but output mentions the removal
        assert r.exit_code == 0, r.output
        assert "assertion" in r.output.lower()
