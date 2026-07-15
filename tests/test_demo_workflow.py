"""End-to-end smoke test: simulate the entire demo PR workflow.

This mirrors what the demo GIF in docs/DEMO.md shows:
1. Fresh repo with a buggy base commit
2. workproof init
3. workproof run -- pytest (tests fail because of the bug)
4. Fix the bug, commit
5. workproof run -- pytest (tests pass)
6. workproof attest --ai-level assisted --agent claude-code
7. workproof verify <receipt> --repo . --expected-head-sha <head>
8. Assert exit 0 (VERIFIED)
"""

from __future__ import annotations

import subprocess

import pytest
from typer.testing import CliRunner

from workproof.cli import app

runner = CliRunner()


@pytest.fixture
def demo_workflow_env(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    repo = tmp_path / "demo-repo"
    repo.mkdir()
    monkeypatch.setenv("HOME", str(home))

    import workproof.keyring as kr

    monkeypatch.setattr(kr, "DEFAULT_KEYRING_DIR", home / ".workproof")
    monkeypatch.setattr(kr, "PRIVATE_KEY_PATH", home / ".workproof" / "id_ed25519")
    monkeypatch.setattr(kr, "PUBLIC_KEY_PATH", home / ".workproof" / "id_ed25519.pub")
    monkeypatch.chdir(repo)

    # Init git
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "demo@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "Demo"], cwd=repo, check=True)
    subprocess.run(["git", "config", "commit.gpgsign", "false"], cwd=repo, check=True)
    subprocess.run(["git", "config", "core.filemode", "false"], cwd=repo, check=True)

    # Base commit: buggy calculator
    (repo / "calculator.py").write_text(
        "def add(a, b):\n    return a - b  # bug\n\ndef multiply(a, b):\n    return a * b\n",
        encoding="utf-8",
    )
    (repo / "test_calculator.py").write_text(
        "from calculator import add, multiply\n\n"
        "def test_add():\n    assert add(2, 3) == 5\n\n"
        "def test_multiply():\n    assert multiply(3, 4) == 12\n",
        encoding="utf-8",
    )
    (repo / ".workproof.yml").write_text(
        'policy_version: "0.1"\nallowed_commands:\n  - pytest\n  - python -m pytest\n',
        encoding="utf-8",
    )
    (repo / ".gitignore").write_text(".workproof/\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "base: buggy add()"], cwd=repo, check=True)
    yield repo


class TestDemoWorkflow:
    def test_full_demo_pr_workflow(self, demo_workflow_env) -> None:
        repo = demo_workflow_env

        # 1. init
        r = runner.invoke(app, ["init"])
        assert r.exit_code == 0, r.output

        # 2. Fix the bug first, then commit (honest workflow: record evidence
        #    only against the commit you're going to attest)
        (repo / "calculator.py").write_text(
            "def add(a, b):\n    return a + b  # fixed\n\ndef multiply(a, b):\n    return a * b\n",
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "fix: add() now adds"], cwd=repo, check=True)
        head_sha = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        # 3. run tests against the fixed commit (should pass)
        r = runner.invoke(app, ["run", "--", "python3", "-m", "pytest"])
        assert r.exit_code == 0, r.output

        # 4. attest
        r = runner.invoke(
            app,
            ["attest", "--ai-level", "assisted", "--agent", "claude-code", "--emit=file"],
        )
        assert r.exit_code == 0, r.output
        assert "VERIFIED" in r.output.upper() or "Workproof Receipt" in r.output

        # 5. verify
        receipt_path = repo / ".workproof" / "receipts" / f"{head_sha}.json"
        assert receipt_path.exists(), f"receipt not written at {receipt_path}"

        r = runner.invoke(
            app,
            ["verify", str(receipt_path), "--repo", str(repo), "--expected-head-sha", head_sha],
        )
        assert r.exit_code == 0, r.output
        assert "VERIFIED" in r.output

        # 6. The receipt includes the AI declaration
        import json

        receipt = json.loads(receipt_path.read_text())
        assert receipt["statement"]["predicate"]["ai_level"] == "assisted"
        assert receipt["statement"]["predicate"]["agent"] == "claude-code"

        # 7. The receipt includes 1 entry (the passing test run against the fixed commit)
        entries = receipt["statement"]["predicate"]["entries"]
        assert len(entries) == 1
        assert entries[0]["exit_code"] == 0  # tests passed

        # 8. Heuristics: no assertions removed, no skip markers (clean fix)
        h = receipt["statement"]["predicate"]["heuristics"]
        assert h["assertions_removed"] == 0
        assert h["new_skip_markers"] == 0
