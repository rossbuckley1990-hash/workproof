# DECISIONS.md

Every non-obvious choice made while building Workproof v0.1, newest at the bottom.
The spec said "make reasonable decisions and log each one" — this is that log.

## D01 — PyPI package name: `workproof-cli`, CLI command: `workproof`

The spec anticipates `workproof` may be taken on PyPI. We pre-emptively publish
under `workproof-cli` to avoid a rename later, but keep the console script
`workproof` so user-facing docs never mention the package name.

## D02 — Canonical JSON via sorted keys + UTF-8 + no extra whitespace

Receipts must hash identically across machines. We serialize every signed
payload with `json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`
and encode as UTF-8 before sha256. This is the same canonicalization rule used
by in-toto and DSSE.

## D03 — Hash chain: sha256 over the canonical JSON of the previous entry

Each session entry stores `prev_hash`. The genesis entry (first in a session
file) uses `prev_hash = null`. Hash is taken over the canonical JSON of the
entry *without* its own `hash` field, so the chain is append-only verifiable
without self-reference.

## D04 — Receipt schema versioned as URI `https://workproof.dev/spec/v0.1`

In-toto Statements use a `predicateType` URI. We use a versioned, HTTPS-scheme
URI even though no server exists yet — this is the convention sigstore/in-toto
follow and keeps forward-compatibility with keyless signing. Verification
refuses unknown predicateTypes rather than best-effort parsing.

## D05 — Ed25519 keys stored raw (32-byte private seed) at `~/.workproof/`

Format: `id_ed25519` (64 bytes = 32 seed + 32 public, OpenSSH-compatible
unencrypted form) and `id_ed25519.pub` (32 bytes). Plain files, 0600 perms.
This matches the OpenSSH ed25519 format closely enough that future migration
to agent-based signing is mechanical. No passphrase support in v0.1 — local
attacker who owns the key already wins.

## D06 — Environment fingerprint is opt-in metadata, never a verification target

We record OS, Python version, and tool versions in each entry. Verification
checks structural integrity, *not* environment match — otherwise receipts
would be non-portable across CI machines. The fingerprint is evidence for a
human reviewer, not a gate.

## D07 — Exit codes for `verify`: 0 verified, 1 tampered, 2 incomplete

Tampered = signature invalid or hash chain broken. Incomplete = signature
valid but the receipt references a tree / commands / SHA that the verifier
cannot reconcile against the repo. This split lets the GitHub Action show
"⚠" vs "✗" distinctly.

## D08 — Heuristics: count, never judge

`attest` reports *counts* (assertions removed, new skip/xfail markers) with
file/line anchors. It never labels a PR "suspicious". The reviewer reads the
table; Workproof refuses to be the jury. This is the single most important
honesty guarantee in the project.

## D09 — GitHub Action is a composite action invoking the installed package

Not a Docker action — we want fast cold-start and the same code path the
contributor used locally. The action `pip install`s the package from PyPI
(or a pinned ref for development) and shells out to `workproof verify`.

## D10 — Sticky comment identity: a hidden HTML comment with a stable marker

GitHub doesn't natively support "update my previous comment". We search the
PR's comments for one containing `<!-- workproof:sticky:v1 -->` and update it
in place; if absent, we post a new one. Exactly one such comment per PR is an
invariant enforced by the action.

## D11 — Re-execute mode compares exit codes only, not output bytes

`reexecute: true` reruns declared commands and compares exit code (and exit
code only) to the recorded one. Output bytes are environment-dependent
(absolute paths, timestamps) and would produce false negatives. Exit code is
the deterministic signal.

## D12 — Test-weakening heuristics ship one true positive AND one known false positive as fixture tests

Per the style guide. Every heuristic has `test_<name>_true_positive` and
`test_<name>_known_false_positive`, the latter documenting the documented
limitation in the test docstring. This is the project's honesty contract
with itself.
