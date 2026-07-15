"""Evidence-laundering fixture test (RED first, then GREEN).

This is the test the reviewer asked for: run tests on commit A, commit broken
code on top as commit B, attest against B. The receipt must NOT verify because
the evidence (pytest pass) was recorded against A, not B.

Before the evidence_freshness check: this test FAILS (receipt wrongly VERIFIED).
After the evidence_freshness check: this test PASSES (receipt correctly INCOMPLETE).
"""

from __future__ import annotations

import json
import subprocess

import pytest
from typer.testing import CliRunner

from workproof.cli import app

runner = CliRunner()


@pytest.fixture
def laundering_repo(tmp_path, monkeypatch):
    """Repo where evidence is recorded on commit A, then broken code is
    committed as B on top, and the receipt is attested against B."""
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

    # Commit A: working code with a passing test
    (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (repo / "test_calc.py").write_text(
        "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
    )
    (repo / ".workproof.yml").write_text(
        'policy_version: "0.1"\n'
        "allowed_commands:\n"
        "  - pytest\n"
        "  - python -m pytest\n"
        "  - python3 -m pytest\n",
        encoding="utf-8",
    )
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "A: working code"], cwd=repo, check=True)
    commit_a = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Init + run tests against A (evidence recorded with head_sha=A, clean tree)
    runner.invoke(app, ["init"])
    runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])

    # Commit B: deliberately break the code ON TOP of A
    (repo / "calc.py").write_text("def add(a, b):\n    return a - b  # BROKEN\n", encoding="utf-8")
    subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-q", "-m", "B: broken code"], cwd=repo, check=True)
    commit_b = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
    ).stdout.strip()

    # Attest against B (HEAD is now B, not A)
    runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit=file"])

    receipt_path = repo / ".workproof" / "receipts" / f"{commit_b}.json"
    assert receipt_path.exists(), f"receipt not written at {receipt_path}"

    return repo, commit_a, commit_b, receipt_path


class TestEvidenceLaundering:
    def test_laundered_receipt_must_not_verify(self, laundering_repo) -> None:
        """The attack: tests passed on A, broken code committed as B, receipt
        attested against B. The receipt's entries record head_sha=A but the
        receipt's subject is B. This is evidence laundering — the pytest pass
        on A does not prove anything about B.

        The verifier MUST reject this with INCOMPLETE (exit 2), not VERIFIED.
        """
        repo, commit_a, commit_b, receipt_path = laundering_repo

        # Sanity: the receipt's subject is B, but entries recorded head_sha=A
        receipt = json.loads(receipt_path.read_text())
        subject_sha = receipt["statement"]["subject"][0]["digest"]["gitSha"]
        assert subject_sha == commit_b, "receipt subject should be commit B"
        entry_head = receipt["statement"]["predicate"]["entries"][0]["git"]["head_sha"]
        assert entry_head == commit_a, "entry head_sha should be commit A (the laundered evidence)"
        assert entry_head != subject_sha, "this is the laundering gap: entry head ≠ subject"

        # The verifier must catch this
        r = runner.invoke(
            app,
            ["verify", str(receipt_path), "--repo", str(repo), "--expected-head-sha", commit_b],
        )
        assert r.exit_code == 2, (
            f"EVIDENCE LAUNDERING GAP: receipt VERIFIED against commit B but evidence was "
            f"recorded on commit A. Output:\n{r.output}"
        )
        assert "INCOMPLETE" in r.output
        assert (
            "evidence" in r.output.lower()
            or "freshness" in r.output.lower()
            or "different tree" in r.output.lower()
        )

    def test_honest_receipt_still_verifies(self, tmp_path, monkeypatch) -> None:
        """Control: when evidence is recorded on the SAME commit that is attested,
        the receipt must still verify. This confirms the freshness check doesn't
        break the honest workflow."""
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

        # Base commit (empty, so HEAD~1 exists for attest)
        (repo / ".workproof.yml").write_text(
            'policy_version: "0.1"\nallowed_commands:\n  - pytest\n  - python -m pytest\n  - python3 -m pytest\n',
            encoding="utf-8",
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "base"], cwd=repo, check=True)

        # Head commit: working code
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (repo / "test_calc.py").write_text(
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "working code"], cwd=repo, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        runner.invoke(app, ["init"])
        runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])
        runner.invoke(app, ["attest", "--ai-level", "assisted", "--agent", "honest", "--emit=file"])

        receipt_path = repo / ".workproof" / "receipts" / f"{head}.json"
        r = runner.invoke(
            app,
            ["verify", str(receipt_path), "--repo", str(repo), "--expected-head-sha", head],
        )
        assert r.exit_code == 0, f"Honest receipt should verify:\n{r.output}"
        assert "VERIFIED" in r.output
        assert "evidence_freshness" in r.output.lower() or "freshness" in r.output.lower()

    def test_dirty_tree_evidence_rejected(self, tmp_path, monkeypatch) -> None:
        """Attack variant: record evidence on a dirty tree (uncommitted changes
        present when the command ran). The entry's dirty_diff_sha256 is non-empty,
        meaning the command's output doesn't correspond to any committed tree.
        The freshness check must reject entries with dirty_diff_sha256 != sha256("").
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

        # Head commit: working code
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
        (repo / "test_calc.py").write_text(
            "from calc import add\n\ndef test_add():\n    assert add(2, 3) == 5\n", encoding="utf-8"
        )
        subprocess.run(["git", "add", "-A"], cwd=repo, check=True)
        subprocess.run(["git", "commit", "-q", "-m", "working code"], cwd=repo, check=True)
        head = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True
        ).stdout.strip()

        runner.invoke(app, ["init"])

        # Make an uncommitted change BEFORE running tests — the tree is now dirty
        (repo / "calc.py").write_text(
            "def add(a, b):\n    return a * b  # uncommitted change\n", encoding="utf-8"
        )

        # Run tests against the dirty tree
        runner.invoke(app, ["run", "--", "python3", "-m", "pytest", "-q"])

        # Revert the uncommitted change so the tree is clean at attest time
        (repo / "calc.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")

        runner.invoke(
            app, ["attest", "--ai-level", "assisted", "--agent", "attacker", "--emit=file"]
        )

        receipt_path = repo / ".workproof" / "receipts" / f"{head}.json"
        receipt = json.loads(receipt_path.read_text())
        entry_dirty = receipt["statement"]["predicate"]["entries"][0]["git"]["dirty_diff_sha256"]

        # The entry's dirty_hash should be non-empty (tree was dirty when pytest ran)
        import hashlib

        clean_hash = hashlib.sha256(b"").hexdigest()
        assert entry_dirty != clean_hash, "entry should have recorded a dirty tree"

        # The verifier must reject this: evidence was recorded against a dirty tree
        r = runner.invoke(
            app,
            ["verify", str(receipt_path), "--repo", str(repo), "--expected-head-sha", head],
        )
        assert r.exit_code == 2, (
            f"DIRTY EVIDENCE GAP: receipt VERIFIED but evidence was recorded against a dirty tree.\n{r.output}"
        )
        assert "INCOMPLETE" in r.output
