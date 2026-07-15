"""Canonical JSON serialization for deterministic hashing.

All signed payloads in Workproof are serialized via this module so that the
same logical object produces the same bytes on every machine. This is the same
canonicalization rule used by in-toto and DSSE: sorted keys, no insignificant
whitespace, UTF-8 preserved (not \\uXXXX-escaped).
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_dumps(obj: Any) -> str:
    """Serialize ``obj`` to a canonical JSON string.

    - Keys are sorted recursively.
    - No extra whitespace (compact separators).
    - Non-ASCII characters are preserved as UTF-8, not escaped.
    """
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def canonical_bytes(obj: Any) -> bytes:
    """Serialize ``obj`` to canonical UTF-8 bytes suitable for hashing."""
    return canonical_dumps(obj).encode("utf-8")


def sha256_hex(obj: Any) -> str:
    """Return the lowercase hex sha256 of the canonical serialization of ``obj``."""
    return hashlib.sha256(canonical_bytes(obj)).hexdigest()


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase hex sha256 of raw ``data``."""
    return hashlib.sha256(data).hexdigest()
