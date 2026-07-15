"""Session recorder: persists hash-chained entries to a JSONL file.

A session file is ``.workproof/session.jsonl``. Each line is a canonical JSON
object representing one entry (a command run, an environment snapshot, etc.).
The chain is maintained across appends: each new entry's ``prev_hash`` is the
hash of the previously-appended line.
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from workproof.canonical import canonical_dumps
from workproof.chain import (
    HASH_FIELD,
    PREV_HASH_FIELD,
    append_entry,
    compute_entry_hash,
    verify_chain,
)

DEFAULT_SESSION_PATH = ".workproof/session.jsonl"


class SessionError(Exception):
    """Raised when a session file is malformed or corrupted."""


class Session:
    """A hash-chained append-only JSONL log.

    Use :meth:`append` to add entries. Use :meth:`entries` to iterate. Use
    :meth:`verify` to check the chain on disk.

    The session is written line-by-line (one canonical JSON object per line).
    Partial writes are not atomic across processes; v0.1 assumes a single
    contributor per session, which matches the typical AI-assisted-PR workflow.
    """

    def __init__(self, path: str | Path = DEFAULT_SESSION_PATH) -> None:
        self.path = Path(path)

    def exists(self) -> bool:
        return self.path.exists()

    def ensure_parent(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def _read_all(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for lineno, line in enumerate(self.path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError as e:
                raise SessionError(f"{self.path}:{lineno}: invalid JSON: {e}") from e
            if not isinstance(obj, dict):
                raise SessionError(
                    f"{self.path}:{lineno}: expected JSON object, got {type(obj).__name__}"
                )
            out.append(obj)
        return out

    def entries(self) -> list[dict[str, Any]]:
        """Return all entries currently in the session file."""
        return self._read_all()

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._read_all())

    def append(self, data: dict[str, Any]) -> dict[str, Any]:
        """Append a new entry to the session.

        ``data`` must not contain ``hash`` or ``prev_hash`` keys. Returns the
        full entry (including the computed ``hash`` and ``prev_hash``).
        """
        if HASH_FIELD in data or PREV_HASH_FIELD in data:
            raise ValueError(f"data must not contain {HASH_FIELD!r} or {PREV_HASH_FIELD!r}")
        existing = self._read_all()
        new_list = append_entry(existing, data)
        entry = new_list[-1]
        self.ensure_parent()
        # Append-only write: open in 'a' mode, write one canonical line.
        with self.path.open("a", encoding="utf-8") as f:
            f.write(canonical_dumps(entry) + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def verify(self) -> bool:
        """Verify the on-disk chain. Raises :class:`workproof.chain.ChainError` on failure."""
        return verify_chain(self._read_all())

    def last_hash(self) -> str | None:
        """Return the hash of the last entry, or ``None`` if the session is empty."""
        entries = self._read_all()
        return entries[-1][HASH_FIELD] if entries else None

    def reset(self) -> None:
        """Delete the session file. Used by `workproof init` to start a clean session."""
        if self.path.exists():
            self.path.unlink()

    @staticmethod
    def compute_hash_for(data: dict[str, Any], prev_hash: str | None) -> str:
        """Compute what the hash of ``data`` would be if appended after ``prev_hash``.

        Pure helper for testing and inspection. Does not write to disk.
        """
        entry = {**data, PREV_HASH_FIELD: prev_hash}
        return compute_entry_hash(entry)
