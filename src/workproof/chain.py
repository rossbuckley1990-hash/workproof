"""Hash-chained session log.

A session is an ordered list of entries. Each entry is a dict with arbitrary
data fields plus two structural fields:

- ``prev_hash``: the hash of the previous entry, or ``None`` for the genesis.
- ``hash``: the sha256 of the canonical JSON of the entry *excluding* its own
  ``hash`` field.

This makes the chain append-only verifiable without self-reference, and makes
any tampering (editing data, swapping links, inserting, deleting) detectable
by recomputation.
"""

from __future__ import annotations

from typing import Any

from workproof.canonical import sha256_hex

HASH_FIELD = "hash"
PREV_HASH_FIELD = "prev_hash"


class ChainError(Exception):
    """Raised when a hash chain fails integrity verification."""


def compute_entry_hash(entry: dict[str, Any]) -> str:
    """Compute the sha256 of ``entry`` excluding its own ``hash`` field.

    The ``hash`` field is removed from a *copy* so the caller's dict is not
    mutated. ``prev_hash`` is included in the hash so the link is part of the
    signed payload.
    """
    payload = {k: v for k, v in entry.items() if k != HASH_FIELD}
    return sha256_hex(payload)


def append_entry(entries: list[dict[str, Any]], data: dict[str, Any]) -> list[dict[str, Any]]:
    """Append a new entry to ``entries`` and return the new list.

    ``data`` must not contain ``hash`` or ``prev_hash`` keys (they are managed
    by this function). The returned list is a new list (the input is not
    mutated) so callers can safely retain the old list for comparison.
    """
    if HASH_FIELD in data or PREV_HASH_FIELD in data:
        raise ValueError(f"data must not contain {HASH_FIELD!r} or {PREV_HASH_FIELD!r}")
    prev_hash = entries[-1][HASH_FIELD] if entries else None
    entry = {**data, PREV_HASH_FIELD: prev_hash}
    entry[HASH_FIELD] = compute_entry_hash(entry)
    return [*entries, entry]


def verify_chain(entries: list[dict[str, Any]]) -> bool:
    """Verify the integrity of a hash-chained list of entries.

    Returns ``True`` if every entry's hash recomputes correctly and every
    ``prev_hash`` correctly links to the previous entry's hash. Raises
    :class:`ChainError` with a diagnostic message otherwise.
    """
    prev_hash: str | None = None
    for i, entry in enumerate(entries):
        if PREV_HASH_FIELD not in entry:
            raise ChainError(f"entry {i}: missing {PREV_HASH_FIELD!r}")
        if entry[PREV_HASH_FIELD] != prev_hash:
            raise ChainError(
                f"entry {i}: prev_hash mismatch — expected {prev_hash!r}, got {entry[PREV_HASH_FIELD]!r}"
            )
        if HASH_FIELD not in entry:
            raise ChainError(f"entry {i}: missing {HASH_FIELD!r}")
        recomputed = compute_entry_hash(entry)
        if recomputed != entry[HASH_FIELD]:
            raise ChainError(
                f"entry {i}: hash mismatch — expected {entry[HASH_FIELD]!r}, recomputed {recomputed!r}"
            )
        prev_hash = entry[HASH_FIELD]
    return True
