"""Attester: bundle a session + git analysis + heuristics into a signed receipt.

This module is the bridge between the recorded session (evidence) and the
verifiable receipt (artifact). It runs the git analysis and heuristics at
attest time so the receipt is self-contained — a verifier needs only the
receipt and the repo, not the original session file.

Per the spec, the receipt is written to ``.workproof/receipts/<headsha>.json``
and a Markdown summary is printed to stdout for pasting into a PR description.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from workproof.crypto import KeyPair
from workproof.git_analysis import all_files_changed, analyze_test_file_changes
from workproof.heuristics import HeuristicResult, analyze_diff
from workproof.receipt import (
    PREDICATE_TYPE,
    STATEMENT_TYPE,
    Receipt,
    build_receipt,
)
from workproof.session import Session

RECEIPTS_DIR = ".workproof/receipts"

VALID_AI_LEVELS = {"none", "assisted", "agent"}


class AttestError(Exception):
    """Raised when attestation cannot be performed."""


def build_statement(
    *,
    session: Session,
    base_sha: str,
    head_sha: str,
    repo: Path,
    ai_level: str,
    agent: str,
    policy_dict: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the in-toto Statement (unsigned) from session + git + heuristics.

    The subject is the head SHA. The predicate carries all the Workproof-specific
    evidence. This is pure — no signing, no I/O — so it can be unit-tested.
    """
    if ai_level not in VALID_AI_LEVELS:
        raise AttestError(f"ai_level must be one of {sorted(VALID_AI_LEVELS)}, got {ai_level!r}")

    entries = session.entries()
    files_changed = all_files_changed(repo, base_sha, head_sha)
    test_files = analyze_test_file_changes(repo, base_sha, head_sha)
    heuristics = analyze_diff(repo, base_sha, head_sha, files_changed)

    # Environment fingerprint is taken from the last entry (most recent state).
    env = entries[-1].get("environment_fingerprint", {}) if entries else {}

    predicate: dict[str, Any] = {
        "schema_version": "0.1",
        "base_sha": base_sha,
        "head_sha": head_sha,
        "files_changed": files_changed,
        "test_files_added": test_files["added"],
        "test_files_modified": test_files["modified"],
        "test_files_deleted": test_files["deleted"],
        "ai_level": ai_level,
        "agent": agent,
        "policy": policy_dict or {},
        "entries": [_entry_public_view(e) for e in entries],
        "heuristics": _heuristics_public_view(heuristics),
        "environment_fingerprint": env,
    }

    return {
        "_type": STATEMENT_TYPE,
        "subject": [
            {
                "name": f"git:{head_sha}",
                "digest": {"gitSha": head_sha, "sha1": head_sha},
            }
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": predicate,
    }


def _entry_public_view(entry: dict[str, Any]) -> dict[str, Any]:
    """Return the entry as-is for inclusion in the receipt.

    The entry's hash and prev_hash are the integrity signal — they MUST be
    preserved verbatim or the verifier's chain check will fail. We do not
    strip or rewrite any fields; the receipt is the witness, not a
    reinterpretation.
    """
    return dict(entry)


def _heuristics_public_view(h: HeuristicResult) -> dict[str, Any]:
    return {
        "assertions_removed": h.assertions_removed,
        "assertions_removed_details": h.assertions_removed_details,
        "new_skip_markers": h.new_skip_markers,
        "new_skip_markers_details": h.new_skip_markers_details,
    }


def attest(
    *,
    session: Session,
    base_sha: str,
    head_sha: str,
    repo: Path,
    ai_level: str,
    agent: str,
    keypair: KeyPair,
    policy_dict: dict[str, Any] | None = None,
    write: bool = True,
) -> Receipt:
    """Build, sign, and (optionally) write a receipt.

    Returns the signed :class:`Receipt`. If ``write`` is True, the receipt is
    written to ``.workproof/receipts/<headsha>.json`` and that path is set as
    ``receipt.receipt_path`` (a non-serialized attribute).
    """
    statement = build_statement(
        session=session,
        base_sha=base_sha,
        head_sha=head_sha,
        repo=repo,
        ai_level=ai_level,
        agent=agent,
        policy_dict=policy_dict,
    )
    receipt = build_receipt(statement=statement, keypair=keypair)
    if write:
        out_dir = Path(RECEIPTS_DIR)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{head_sha}.json"
        import json

        out_path.write_text(
            json.dumps(receipt.to_dict(), indent=2, sort_keys=True, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        receipt.receipt_path = str(out_path)  # type: ignore[attr-defined]
    return receipt
