"""Tests for the hash-chained session log.

Written BEFORE implementation per spec. The chain must:
1. Hash each entry deterministically (excluding its own `hash` field).
2. Link each entry to the previous via `prev_hash`.
3. Detect tampering, reordering, insertion, and deletion.
"""

from __future__ import annotations

import pytest

from workproof.chain import (
    ChainError,
    compute_entry_hash,
    verify_chain,
)


class TestComputeEntryHash:
    def test_excludes_the_hash_field_itself(self) -> None:
        entry = {"cmd": "pytest", "prev_hash": None, "hash": "deadbeef"}
        h = compute_entry_hash(entry)
        # Recompute without the hash field; must match.
        import hashlib

        from workproof.canonical import canonical_bytes

        reference = hashlib.sha256(
            canonical_bytes({"cmd": "pytest", "prev_hash": None})
        ).hexdigest()
        assert h == reference

    def test_is_stable(self) -> None:
        entry = {"cmd": "pytest", "prev_hash": "abc", "hash": "ignore"}
        assert compute_entry_hash(entry) == compute_entry_hash(entry)

    def test_changes_when_data_changes(self) -> None:
        a = {"cmd": "pytest", "prev_hash": None, "hash": ""}
        b = {"cmd": "ruff", "prev_hash": None, "hash": ""}
        assert compute_entry_hash(a) != compute_entry_hash(b)

    def test_changes_when_prev_hash_changes(self) -> None:
        a = {"cmd": "pytest", "prev_hash": "aaa", "hash": ""}
        b = {"cmd": "pytest", "prev_hash": "bbb", "hash": ""}
        assert compute_entry_hash(a) != compute_entry_hash(b)


class TestVerifyChain:
    def test_empty_chain_is_valid(self) -> None:
        assert verify_chain([]) is True

    def test_single_entry_genesis_with_null_prev_hash_is_valid(self) -> None:
        e = {"cmd": "pytest", "prev_hash": None}
        e["hash"] = compute_entry_hash(e)
        assert verify_chain([e]) is True

    def test_chain_of_three_is_valid_when_hashes_link(self) -> None:
        from workproof.chain import append_entry

        entries = []
        for cmd in ["pytest", "ruff", "build"]:
            entries = append_entry(entries, {"cmd": cmd})
        assert verify_chain(entries) is True

    def test_tampered_entry_data_is_detected(self) -> None:
        from workproof.chain import append_entry

        entries = []
        for cmd in ["pytest", "ruff"]:
            entries = append_entry(entries, {"cmd": cmd})
        entries[0]["cmd"] = "echo hacked"
        with pytest.raises(ChainError):
            verify_chain(entries)

    def test_tampered_hash_is_detected(self) -> None:
        from workproof.chain import append_entry

        entries = []
        for cmd in ["pytest", "ruff"]:
            entries = append_entry(entries, {"cmd": cmd})
        entries[1]["hash"] = "0" * 64
        with pytest.raises(ChainError):
            verify_chain(entries)

    def test_broken_link_is_detected(self) -> None:
        from workproof.chain import append_entry

        entries = []
        for cmd in ["pytest", "ruff", "build"]:
            entries = append_entry(entries, {"cmd": cmd})
        entries[1]["prev_hash"] = "0" * 64
        with pytest.raises(ChainError):
            verify_chain(entries)

    def test_insertion_is_detected(self) -> None:
        from workproof.chain import append_entry

        entries = []
        for cmd in ["pytest", "ruff"]:
            entries = append_entry(entries, {"cmd": cmd})
        # Insert a fake middle entry with a self-consistent hash but wrong prev_hash
        fake = {"cmd": "evil", "prev_hash": entries[0]["hash"]}
        fake["hash"] = compute_entry_hash(fake)
        entries.insert(1, fake)
        with pytest.raises(ChainError):
            verify_chain(entries)

    def test_genesis_with_non_null_prev_hash_is_rejected(self) -> None:
        e = {"cmd": "pytest", "prev_hash": "abc"}
        e["hash"] = compute_entry_hash(e)
        with pytest.raises(ChainError):
            verify_chain([e])
