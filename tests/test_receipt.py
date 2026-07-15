"""Tests for the receipt builder and serializer.

Golden-file approach: a known-good receipt is generated from a fixed key seed
and a fixed session, then byte-compared against a recorded golden file. If the
serialization changes, the test fails and the developer must explicitly update
the golden file (regenerate_golden = True).
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from workproof.canonical import canonical_bytes
from workproof.crypto import KeyPair, derive_public_key
from workproof.receipt import (
    PREDICATE_TYPE,
    ReceiptError,
    build_receipt,
    parse_receipt,
    verify_receipt_signature,
)

FIXED_SEED = b"\x01" * 32
FIXED_PUBKEY = derive_public_key(FIXED_SEED)

GOLDEN_DIR = Path(__file__).parent / "golden"


def _fixed_keypair() -> KeyPair:
    return KeyPair(private_seed=FIXED_SEED, public_key=FIXED_PUBKEY)


def _sample_statement() -> dict:
    """A small, deterministic statement used across tests."""
    return {
        "_type": "https://in-toto.io/Statement/v1",
        "subject": [
            {"name": "git:abc123", "digest": {"sha256": "abc123"}},
        ],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "schema_version": "0.1",
            "base_sha": "0000000",
            "head_sha": "abc123",
            "files_changed": ["src/app.py"],
            "test_files_added": ["tests/test_app.py"],
            "test_files_modified": [],
            "test_files_deleted": [],
            "ai_level": "assisted",
            "agent": "claude-code",
            "policy": {"test_command": "pytest", "build_command": ""},
            "entries": [],
            "heuristics": {
                "assertions_removed": 0,
                "assertions_removed_details": [],
                "new_skip_markers": 0,
                "new_skip_markers_details": [],
            },
            "environment_fingerprint": {"os": "linux", "python": "3.12.13", "tools": {}},
        },
    }


class TestReceiptRoundTrip:
    def test_build_then_parse_round_trips(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        parsed = parse_receipt(receipt.to_dict())
        assert parsed.statement == stmt
        assert parsed.public_key_bytes == kp.public_key

    def test_payload_matches_canonical_statement(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        # payload must be base64 of canonical JSON of the statement
        expected = base64.b64encode(canonical_bytes(stmt)).decode("ascii")
        assert receipt.payload == expected

    def test_signature_verifies(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        assert verify_receipt_signature(receipt) is True

    def test_tampered_statement_breaks_signature(self) -> None:
        """Mutate the embedded statement; signature over original payload still
        'verifies' cryptographically (sig is over payload, not statement), but
        parse_receipt must reject the mismatch. Test both layers."""
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        # Cryptographic layer: signature over payload still verifies (payload unchanged)
        assert verify_receipt_signature(receipt) is True
        # Structural layer: payload no longer matches canonical(statement) → reject
        receipt.statement["predicate"]["ai_level"] = "none"
        with pytest.raises(ReceiptError, match="tampered"):
            parse_receipt(receipt.to_dict())

    def test_tampered_payload_breaks_signature(self) -> None:
        """When the payload itself is swapped, the cryptographic signature fails."""
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        # Replace payload with bytes that don't match the signature
        receipt.payload = base64.b64encode(b'{"different": true}').decode("ascii")
        # The signature check on the (now mismatched) payload should fail
        assert verify_receipt_signature(receipt) is False

    def test_swapped_public_key_breaks_signature(self) -> None:
        from workproof.crypto import generate_keypair

        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        other = generate_keypair()
        receipt.public_key = other.public_key
        assert verify_receipt_signature(receipt) is False


class TestReceiptGoldenFile:
    """Byte-stable golden file: receipt for fixed key + fixed statement."""

    GOLDEN_PATH = GOLDEN_DIR / "receipt_v0.1.json"

    def test_golden_file_matches(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        actual = receipt.to_dict()
        actual_json = json.dumps(actual, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

        if not self.GOLDEN_PATH.exists():
            GOLDEN_DIR.mkdir(parents=True, exist_ok=True)
            self.GOLDEN_PATH.write_text(actual_json + "\n", encoding="utf-8")
            pytest.fail(f"golden file did not exist; wrote {self.GOLDEN_PATH}; rerun to verify")

        expected_json = self.GOLDEN_PATH.read_text(encoding="utf-8").rstrip("\n")
        assert actual_json == expected_json, (
            "receipt serialization drifted from golden file. "
            "If intentional, delete tests/golden/receipt_v0.1.json and rerun."
        )


class TestReceiptErrors:
    def test_parse_rejects_wrong_schema_version(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        d = receipt.to_dict()
        d["schema_version"] = "0.2"
        with pytest.raises(ReceiptError, match="schema_version"):
            parse_receipt(d)

    def test_parse_rejects_missing_payload(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        d = receipt.to_dict()
        del d["payload"]
        with pytest.raises(ReceiptError, match="payload"):
            parse_receipt(d)

    def test_parse_rejects_missing_signature(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        d = receipt.to_dict()
        d["signatures"] = []
        with pytest.raises(ReceiptError, match="signature"):
            parse_receipt(d)

    def test_parse_rejects_payload_statement_mismatch(self) -> None:
        """If payload != canonical(statement), the receipt is tampered."""
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        d = receipt.to_dict()
        # Mutate statement but leave payload
        d["statement"]["predicate"]["ai_level"] = "none"
        with pytest.raises(ReceiptError, match="payload.*mismatch|tampered"):
            parse_receipt(d)


class TestReceiptMarkdown:
    def test_to_markdown_includes_key_facts(self) -> None:
        kp = _fixed_keypair()
        stmt = _sample_statement()
        receipt = build_receipt(statement=stmt, keypair=kp)
        md = receipt.to_markdown(receipt_path=".workproof/receipts/abc123.json")
        assert "workproof" in md.lower()
        assert "abc123" in md  # head SHA
        assert "assisted" in md  # ai_level
        assert "claude-code" in md  # agent
        assert ".workproof/receipts/abc123.json" in md  # path
        # Sticky marker for the GitHub Action to find
        assert "<!-- workproof:sticky:v1 -->" in md
