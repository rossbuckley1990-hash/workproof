# Receipt Schema

Workproof v0.1 receipts are [DSSE-style envelopes](https://github.com/secure-systems-lab/dsse)
wrapping [in-toto Statements](https://github.com/in-toto/statements).

## Top-level envelope

```json
{
  "schema_version": "0.1",
  "payload_type": "application/vnd.in-toto+json",
  "payload": "<base64 of canonical JSON of statement>",
  "signatures": [
    {
      "sig": "<base64 ed25519 signature, 64 bytes>",
      "keyid": "ed25519"
    }
  ],
  "public_key": "<base64 ed25519 public key, 32 bytes>",
  "statement": { ... the in-toto Statement, also embedded for human readers ... }
}
```

### Field semantics

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | `"0.1"`. Verification refuses unknown versions. Forward-compatible: v0.2 receipts will use `"0.2"` and the v0.1 verifier will refuse them (not best-effort parse). |
| `payload_type` | string | Always `"application/vnd.in-toto+json"` (DSSE standard). |
| `payload` | string (base64) | Base64 of the canonical JSON serialization of `statement`. The signature is over these bytes. |
| `signatures` | array | One signature in v0.1. Future versions may add sigstore certificates. |
| `signatures[].sig` | string (base64) | 64-byte ed25519 detached signature. |
| `signatures[].keyid` | string | `"ed25519"` in v0.1. Future versions: `"sigstore"` for keyless. |
| `public_key` | string (base64) | 32-byte ed25519 public key. Used to verify the signature. Optional in future keyless versions. |
| `statement` | object | The in-toto Statement, embedded for human readability. **Untrusted**: the verifier re-canonicalizes this and checks it matches `payload`. A tampered `statement` field is detected as payload/statement mismatch. |

## in-toto Statement

```json
{
  "_type": "https://in-toto.io/Statement/v1",
  "subject": [
    {
      "name": "git:<head_sha>",
      "digest": {
        "gitSha": "<40-char hex>",
        "sha1": "<40-char hex, same as gitSha>"
      }
    }
  ],
  "predicateType": "https://workproof.dev/spec/v0.1",
  "predicate": { ... Workproof-specific predicate ... }
}
```

### Subject

The subject is always the head git SHA of the PR. The `digest` contains both
`gitSha` (Workproof's preferred name) and `sha1` (in-toto's standard name)
pointing to the same value, for compatibility with in-toto tooling.

### PredicateType

`https://workproof.dev/spec/v0.1` — a versioned URI. No server exists at this
URL today; the URI is a namespaced identifier, following in-toto convention.
Future versions will use `v0.2`, `v0.3`, etc. Verification of v0.1 receipts
must never break when v0.2 is released.

## Predicate

```json
{
  "schema_version": "0.1",
  "base_sha": "<40-char hex>",
  "head_sha": "<40-char hex>",
  "files_changed": ["path/to/file.py", ...],
  "test_files_added": ["tests/test_new.py", ...],
  "test_files_modified": ["tests/test_existing.py", ...],
  "test_files_deleted": ["tests/test_old.py", ...],
  "ai_level": "none" | "assisted" | "agent",
  "agent": "<string, e.g. claude-code>",
  "policy": {
    "policy_version": "0.1",
    "allowed_commands": ["pytest", "ruff check ."],
    "default_ai_level": "assisted"
  },
  "entries": [ ... hash-chained session entries ... ],
  "heuristics": {
    "assertions_removed": 0,
    "assertions_removed_details": [
      {"file": "tests/test_x.py", "line": 12, "content": "assert x == 1"}
    ],
    "new_skip_markers": 0,
    "new_skip_markers_details": [
      {"file": "tests/test_x.py", "line": 5, "content": "@pytest.mark.skip"}
    ]
  },
  "environment_fingerprint": {
    "os": "Linux-5.10.0-x86_64",
    "python": "3.12.13",
    "tools": {
      "git": "git version 2.47.3",
      "python": "3.12.13"
    }
  }
}
```

### Predicate field semantics

| Field | Type | Description |
|---|---|---|
| `schema_version` | string | Predicate schema version. Same as envelope `schema_version` in v0.1. |
| `base_sha` | string | The base git SHA (PR target's merge-base with head). |
| `head_sha` | string | The head git SHA (PR's head commit). Must match `subject[0].digest.gitSha`. |
| `files_changed` | array<string> | All paths touched base..head, sorted, deduplicated. |
| `test_files_added/modified/deleted` | array<string> | Subset of `files_changed` that are test files (per language convention). |
| `ai_level` | string | `none`, `assisted`, or `agent`. Self-declared. |
| `agent` | string | Name of the AI agent used (e.g., `claude-code`, `copilot`, `cursor`). Free-form. |
| `policy` | object | The project's `.workproof.yml` policy, recorded for reviewer context. Not a verification target. |
| `entries` | array<object> | The hash-chained session entries. See below. |
| `heuristics` | object | Counts + details of test-weakening signals. Informational; never a verification target. |
| `environment_fingerprint` | object | OS, Python version, tool versions. Informational; never a verification target (D06). |

### Session entry

Each entry in `entries` is a hash-chained record of one executed command:

```json
{
  "kind": "command",
  "argv": ["pytest", "-x"],
  "cwd_relative": ".",
  "git": {
    "head_sha": "<40-char hex>",
    "dirty_diff_sha256": "<64-char hex>"
  },
  "started_at": "2026-07-15T11:53:25Z",
  "ended_at": "2026-07-15T11:53:27Z",
  "exit_code": 0,
  "stdout_sha256": "<64-char hex>",
  "stderr_sha256": "<64-char hex>",
  "evidence_paths": {
    "stdout": ".workproof/evidence/<id>.out.gz",
    "stderr": ".workproof/evidence/<id>.err.gz"
  },
  "environment_fingerprint": { ... },
  "prev_hash": null | "<64-char hex>",
  "hash": "<64-char hex>"
}
```

The first entry has `prev_hash: null` (genesis). Each subsequent entry's
`prev_hash` equals the previous entry's `hash`. The `hash` field is the sha256
of the canonical JSON of the entry *excluding* its own `hash` field. Tampering
with any field, reordering, inserting, or deleting entries is detected by
`workproof verify`'s hash-chain check.

## Canonical serialization

All payloads are serialized via:

```python
json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
```

Then encoded as UTF-8. This is the same rule used by in-toto and DSSE.

## Forward compatibility

Verification of v0.1 receipts must never break when:

- v0.2 is released with keyless signing support. The v0.2 verifier must still
  accept v0.1 receipts signed with embedded ed25519 keys.
- v0.3 is released with sigstore bundles. The v0.3 verifier must still accept
  v0.1 receipts.
- A future predicate adds new fields. The v0.1 verifier ignores unknown
  predicate fields (it does not refuse them).

Verification of v0.1 receipts **will** break (intentionally) when:

- The `schema_version` field is changed (a v0.1 verifier refuses `"0.2"` and
  later).
- The `predicateType` URI is changed (a v0.1 verifier refuses unknown URIs).
- The `payload_type` is changed (a v0.1 verifier refuses unknown payload types).
