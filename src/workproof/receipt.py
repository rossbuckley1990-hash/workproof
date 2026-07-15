"""Receipt construction, serialization, signature, and verification.

A Workproof receipt is a DSSE-style envelope wrapping an in-toto Statement.

Envelope shape (``to_dict`` output)::

    {
      "schema_version": "0.1",
      "payload_type": "application/vnd.in-toto+json",
      "payload": "<base64 of canonical JSON of statement>",
      "signatures": [{"sig": "<base64 ed25519 sig>", "keyid": "ed25519"}],
      "public_key": "<base64 ed25519 pubkey, 32 bytes>",
      "statement": { ...the in-toto Statement, also embedded for human readers... }
    }

The embedded ``statement`` is **not** trusted on verify — the verifier
re-serializes it canonically and checks it matches ``payload``. This means a
tamperer cannot sneak changes via the human-readable copy.

Design for forward-compatibility: when sigstore keyless signing lands, the
``signatures`` array grows a ``certificate`` field and ``public_key`` becomes
optional. Verification of v0.1 receipts must never break — the
``schema_version`` field is the gate.
"""

from __future__ import annotations

import base64
import textwrap
from dataclasses import dataclass
from typing import Any

from workproof.canonical import canonical_bytes
from workproof.crypto import KeyPair, sign, verify

SCHEMA_VERSION = "0.1"
PREDICATE_TYPE = "https://workproof.dev/spec/v0.1"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PAYLOAD_TYPE = "application/vnd.in-toto+json"
KEYID = "ed25519"

STICKY_MARKER = "<!-- workproof:sticky:v1 -->"


class ReceiptError(Exception):
    """Raised when a receipt is structurally invalid or tampered."""


@dataclass
class Receipt:
    """A signed Workproof receipt (DSSE-style envelope around an in-toto Statement).

    Fields mirror the DSSE Envelope plus the embedded human-readable statement
    and the public key needed for verification.
    """

    schema_version: str
    payload_type: str
    payload: str  # base64-encoded canonical JSON of `statement`
    signatures: list[dict[str, str]]
    public_key: str  # base64-encoded 32-byte ed25519 public key
    statement: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "payload_type": self.payload_type,
            "payload": self.payload,
            "signatures": self.signatures,
            "public_key": self.public_key,
            "statement": self.statement,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Receipt:
        return cls(
            schema_version=d["schema_version"],
            payload_type=d["payload_type"],
            payload=d["payload"],
            signatures=d["signatures"],
            public_key=d["public_key"],
            statement=d["statement"],
        )

    # ----- convenience accessors -----

    @property
    def signature_bytes(self) -> bytes:
        if not self.signatures:
            raise ReceiptError("no signatures present")
        return base64.b64decode(self.signatures[0]["sig"])

    @property
    def public_key_bytes(self) -> bytes:
        return base64.b64decode(self.public_key)

    @property
    def payload_bytes(self) -> bytes:
        return base64.b64decode(self.payload)

    # ----- human-readable rendering -----

    def to_markdown(self, receipt_path: str | None = None) -> str:
        """Render a Markdown summary suitable for pasting into a PR description.

        Includes the sticky marker so the GitHub Action can find and update
        exactly one comment per PR.
        """
        p = self.statement.get("predicate", {})
        h = p.get("heuristics", {})
        env = p.get("environment_fingerprint", {})
        head_sha = _extract_head_sha(self.statement)

        path_line = f"\nReceipt: `{receipt_path}`" if receipt_path else ""
        files = p.get("files_changed", []) or []
        files_block = "\n".join(f"- `{f}`" for f in files[:20]) or "- _(none)_"
        if len(files) > 20:
            files_block += f"\n- _…and {len(files) - 20} more_"

        sig_b64 = self.signatures[0]["sig"] if self.signatures else ""
        sig_short = sig_b64[:16] + "…" if sig_b64 else "_(none)_"
        pubkey_short = self.public_key[:16] + "…" if self.public_key else "_(none)_"

        return textwrap.dedent(
            f"""\
            {STICKY_MARKER}
            ## Workproof Receipt

            | Check | Value |
            |---|---|
            | Head SHA | `{head_sha}` |
            | AI level | `{p.get("ai_level", "unknown")}` |
            | Agent | `{p.get("agent", "unknown")}` |
            | Assertions removed | {h.get("assertions_removed", 0)} |
            | New skip/xfail markers | {h.get("new_skip_markers", 0)} |
            | Test files added/modified/deleted | {len(p.get("test_files_added", []))} / {len(p.get("test_files_modified", []))} / {len(p.get("test_files_deleted", []))} |
            | Signature | `{sig_short}` |
            | Public key | `{pubkey_short}` |

            <details><summary>Files changed ({len(files)})</summary>

            {files_block}

            </details>
            <details><summary>Environment fingerprint</summary>

            - OS: `{env.get("os", "unknown")}`
            - Python: `{env.get("python", "unknown")}`
            - Tools: `{", ".join(f"{k}={v}" for k, v in env.get("tools", {}).items()) or "none"}`

            </details>
            Verify locally: `workproof verify {receipt_path or "<receipt.json>"}`{path_line}
            """
        )


# ----- construction -----


def build_receipt(statement: dict[str, Any], keypair: KeyPair) -> Receipt:
    """Build and sign a receipt envelope around ``statement``.

    The statement must already be a complete in-toto Statement
    (``_type``, ``subject``, ``predicateType``, ``predicate``).
    """
    payload_bytes = canonical_bytes(statement)
    payload_b64 = base64.b64encode(payload_bytes).decode("ascii")
    sig = sign(keypair.private_seed, payload_bytes)
    sig_b64 = base64.b64encode(sig).decode("ascii")
    pub_b64 = base64.b64encode(keypair.public_key).decode("ascii")
    return Receipt(
        schema_version=SCHEMA_VERSION,
        payload_type=PAYLOAD_TYPE,
        payload=payload_b64,
        signatures=[{"sig": sig_b64, "keyid": KEYID}],
        public_key=pub_b64,
        statement=statement,
    )


# ----- parsing & verification -----


def parse_receipt(d: dict[str, Any]) -> Receipt:
    """Parse a receipt dict, checking structural invariants.

    Raises :class:`ReceiptError` for:
    - unknown ``schema_version`` (forward-compat gate)
    - missing envelope fields
    - payload / statement mismatch (tampered human-readable copy)
    - missing or malformed signature
    """
    if d.get("schema_version") != SCHEMA_VERSION:
        raise ReceiptError(
            f"unsupported schema_version {d.get('schema_version')!r}; "
            f"this verifier only supports {SCHEMA_VERSION!r}"
        )
    for k in ("payload_type", "payload", "signatures", "public_key", "statement"):
        if k not in d:
            raise ReceiptError(f"missing field {k!r}")
    if d["payload_type"] != PAYLOAD_TYPE:
        raise ReceiptError(f"unsupported payload_type {d['payload_type']!r}")
    if not isinstance(d["signatures"], list) or not d["signatures"]:
        raise ReceiptError("signatures must be a non-empty list")
    for s in d["signatures"]:
        if "sig" not in s:
            raise ReceiptError("signature missing 'sig' field")

    stmt = d["statement"]
    if not isinstance(stmt, dict):
        raise ReceiptError("statement must be an object")
    if stmt.get("_type") != STATEMENT_TYPE:
        raise ReceiptError(f"statement _type must be {STATEMENT_TYPE!r}, got {stmt.get('_type')!r}")
    if stmt.get("predicateType") != PREDICATE_TYPE:
        raise ReceiptError(
            f"statement predicateType must be {PREDICATE_TYPE!r}, got {stmt.get('predicateType')!r}"
        )

    # Critical: the embedded statement must canonically hash to the payload.
    expected_payload = base64.b64encode(canonical_bytes(stmt)).decode("ascii")
    if expected_payload != d["payload"]:
        raise ReceiptError("payload does not match canonical serialization of statement (tampered)")

    return Receipt.from_dict(d)


def verify_receipt_signature(receipt: Receipt) -> bool:
    """Return ``True`` iff the signature is a valid ed25519 signature over the payload."""
    return verify(
        receipt.public_key_bytes,
        receipt.payload_bytes,
        receipt.signature_bytes,
    )


def _extract_head_sha(statement: dict[str, Any]) -> str:
    """Pull the head SHA out of an in-toto Statement (subject digest sha256)."""
    for sub in statement.get("subject", []):
        digests = sub.get("digest", {})
        if "gitSha" in digests:
            return digests["gitSha"]
        if "sha256" in digests:
            return digests["sha256"]
    return "unknown"
