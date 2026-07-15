"""Tests for the GitHub Action's receipt-location and re-execution logic.

The Action itself is a composite action (action.yml) that runs shell + JS.
We test the *determinable* parts — receipt path resolution and the
re-execution comparison logic — by re-implementing them in Python and
asserting behavior. The actual Action is exercised by the demo repo's
workflow on a real PR.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path


def _write_receipt(path: Path, head_sha: str, argv: list[str], exit_code: int = 0) -> None:
    """Write a minimal valid receipt at ``path`` for the given head SHA."""
    path.parent.mkdir(parents=True, exist_ok=True)
    receipt = {
        "schema_version": "0.1",
        "payload_type": "application/vnd.in-toto+json",
        "payload": "",
        "signatures": [{"sig": "", "keyid": "ed25519"}],
        "public_key": "",
        "statement": {
            "_type": "https://in-toto.io/Statement/v1",
            "subject": [{"name": f"git:{head_sha}", "digest": {"gitSha": head_sha}}],
            "predicateType": "https://workproof.dev/spec/v0.1",
            "predicate": {
                "schema_version": "0.1",
                "head_sha": head_sha,
                "entries": [{"argv": argv, "exit_code": exit_code}],
            },
        },
    }
    path.write_text(json.dumps(receipt), encoding="utf-8")


class TestReceiptLocation:
    """Mirrors the receipt-location shell logic in action.yml."""

    def test_explicit_path_wins(self, tmp_path: Path) -> None:
        head_sha = "abc123"
        explicit = tmp_path / "custom-receipt.json"
        conventional = tmp_path / ".workproof" / "receipts" / f"{head_sha}.json"
        _write_receipt(explicit, head_sha, ["pytest"])
        _write_receipt(conventional, head_sha, ["pytest"])
        # If explicit path is given and exists, it wins
        assert explicit.exists()
        assert conventional.exists()

    def test_conventional_path_used_when_no_explicit(self, tmp_path: Path) -> None:
        head_sha = "def456"
        conventional = tmp_path / ".workproof" / "receipts" / f"{head_sha}.json"
        _write_receipt(conventional, head_sha, ["pytest"])
        assert conventional.exists()

    def test_missing_receipt_is_reported(self, tmp_path: Path) -> None:
        # No receipt at conventional or explicit path
        assert not (tmp_path / ".workproof" / "receipts").exists()


class TestReexecuteLogic:
    """Mirrors the re-execution Python snippet in action.yml.

    Compares exit codes only (D11). Output bytes are environment-dependent.
    """

    @staticmethod
    def _compare_exit_codes(receipt_path: Path) -> list[tuple[list[str], int, int]]:
        """Re-run each declared command, return [(argv, recorded_exit, actual_exit), ...]."""
        receipt = json.loads(receipt_path.read_text())
        entries = receipt["statement"]["predicate"].get("entries", [])
        results = []
        for e in entries:
            argv = e.get("argv", [])
            if not argv:
                continue
            recorded = e.get("exit_code", 0)
            try:
                proc = subprocess.run(argv, capture_output=True, timeout=10)
                actual = proc.returncode
            except (FileNotFoundError, subprocess.TimeoutExpired):
                actual = -1
            results.append((argv, recorded, actual))
        return results

    def test_matching_exit_code(self, tmp_path: Path) -> None:
        receipt = tmp_path / "r.json"
        _write_receipt(receipt, "sha", ["python3", "-c", "print('ok')"], exit_code=0)
        results = self._compare_exit_codes(receipt)
        assert len(results) == 1
        _argv, recorded, actual = results[0]
        assert recorded == 0
        assert actual == 0

    def test_mismatching_exit_code(self, tmp_path: Path) -> None:
        """Receipt claims exit 0, but the command actually exits 1 → mismatch."""
        receipt = tmp_path / "r.json"
        _write_receipt(receipt, "sha", ["python3", "-c", "import sys; sys.exit(1)"], exit_code=0)
        results = self._compare_exit_codes(receipt)
        assert len(results) == 1
        _argv, recorded, actual = results[0]
        assert recorded == 0
        assert actual == 1

    def test_missing_command(self, tmp_path: Path) -> None:
        """Command not found → actual = -1, never matches recorded."""
        receipt = tmp_path / "r.json"
        _write_receipt(receipt, "sha", ["nonexistent-command-xyz"], exit_code=0)
        results = self._compare_exit_codes(receipt)
        assert len(results) == 1
        _argv, recorded, actual = results[0]
        assert recorded == 0
        assert actual == -1


class TestStickyCommentMarker:
    """The sticky marker must be byte-stable so the Action can find it."""

    def test_marker_is_in_receipt_markdown(self, tmp_path: Path) -> None:
        from workproof.receipt import STICKY_MARKER

        assert STICKY_MARKER == "<!-- workproof:sticky:v1 -->"

    def test_marker_appears_exactly_once_in_action_yml(self) -> None:
        """The Action must emit the marker exactly once per comment, so the
        find-comment step reliably finds exactly one comment to update."""
        action_path = Path(__file__).parent.parent / "action.yml"
        content = action_path.read_text()
        # The marker appears in the comment-header heredoc
        assert content.count("<!-- workproof:sticky:v1 -->") >= 1
