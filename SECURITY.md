# SECURITY.md

## What Workproof proves

A Workproof receipt is a signed in-toto Statement that proves **specific
commands ran against a specific git tree and produced specific outputs**.

Each numbered claim maps to a named verifier check. If a check is skipped
(missing `--repo`, `--policy`, or `--expected-head-sha`), it renders as
`○ skipped` in the output and is never counted as a pass. A receipt is only
`VERIFIED` when every applicable check passes.

1. **Specific commands ran with recorded exit codes.**
   → `hash_chain` check: entries are intact and hash-chained. Each entry's
   `argv` and `exit_code` are under the signature. (Schema 0.1+)

2. **Commands ran against the attested tree (not a different commit, not a dirty tree).**
   → `evidence_freshness` check (schema 0.2 only): every entry's
   `git.head_sha` must equal the receipt's subject SHA, AND
   `git.dirty_diff_sha256` must equal `sha256("")` (clean working tree).
   This is the anti-laundering check — without it, a contributor could record
   evidence on commit A and attest it against commit B. Schema 0.1 receipts
   skip this check with a visible `○ skipped` status; the reviewer sees the gap.

3. **The stdout and stderr hashes match what was captured.**
   → `receipt_structure` + `signature` checks: the payload is the canonical
   serialization of the statement, and the signature covers the payload. Any
   tampering with stdout/stderr hashes breaks the signature. (Schema 0.1+)

4. **The session entries are hash-chained and the chain is intact.**
   → `hash_chain` check: recomputes every entry's hash and verifies the
   `prev_hash` links. Detects tampering, reordering, insertion, deletion.
   (Schema 0.1+)

5. **The receipt was signed with an ed25519 key whose public half is embedded.**
   → `signature` check: verifies the detached ed25519 signature against the
   embedded public key. (Schema 0.1+)

6. **The receipt's subject SHA matches the PR head (or is an ancestor of it).**
   → `head_sha` check: the receipt's `subject.gitSha` must match
   `--expected-head-sha` exactly, or (with `--allow-ancestor`) be an ancestor
   of it. Prevents replay across PRs. (Schema 0.1+)

7. **Declared commands are a subset of project policy.**
   → `command_policy` check: every recorded `argv` must match an entry in
   `.workproof.yml`'s `allowed_commands`. Prevents attesting commands the
   project didn't authorize. (Schema 0.1+; skipped if no `--policy` given)

8. **The declared AI level and agent name are attested under signature.**
   → `ai_declaration` check (informational): reads `ai_level` and `agent`
   from the signed predicate. Self-declared; Workproof does not verify
   honesty. (Schema 0.1+)

9. **Counts of removed assertions and new skip/xfail markers are reported.**
   → `heuristics` check (informational, never fails verification): reports
   counts with file:line anchors. The reviewer decides if the changes are
   justified. (Schema 0.1+)

**Important:** claims 1–7 are enforcement checks that can fail verification
(exit 1 or 2). Claims 8–9 are informational — they never change the exit code,
because Workproof refuses to be the jury on intent.

## What Workproof does NOT prove

Read this twice. Overclaiming here will get the project destroyed on Hacker News.

- **Not comprehension.** A receipt proves commands ran, not that the
  contributor understood the output. A contributor can run `pytest`, see it
  fail, and still submit the receipt.
- **Not code quality.** There is no LLM judgment, no score, no "suspicion"
  rating. Workproof refuses to be the jury.
- **Not absence of malice.** A local attacker holding the private key can
  fabricate a session around doctored tests. The signature only proves the
  receipt was signed by the key holder — not that the key holder is honest.
- **Not that the AI was honest.** The `--ai-level` flag is self-declared.
  A contributor can label an `agent` PR as `assisted` and Workproof will not
  catch the lie.
- **Not that the tests are good.** Removing an assertion is reported as a
  *count*, not a *verdict*. Sometimes removing an assertion is correct (the
  test was wrong). The reviewer decides.

## Threat model

### Local attacker holding the key

**Attack:** The contributor generates a keypair, fabricates a session log with
doctored entries, signs a receipt, and submits it.

**Mitigations:**
- CI re-execution mode (`reexecute: true` in the Action) reruns declared
  commands and compares exit codes. A fabricated session that claims
  `pytest` passed will fail re-execution if the tests don't actually pass at
  the head SHA.
- Policy pinning (`.workproof.yml`) requires specific commands be present.
- Future keyless signing (sigstore) moves the key out of the contributor's
  hands entirely; see [`ROADMAP.md`](ROADMAP.md).

**Residual risk:** A local attacker can still fabricate a session around
doctored tests that pass. Workproof raises the cost of lying; it does not
eliminate lying.

### Replay attack

**Attack:** A contributor takes a valid receipt from PR #1 and pastes it into
PR #2.

**Mitigation:** The receipt's subject is the head SHA. The Action passes
`--expected-head-sha` so a receipt from a different commit is rejected with
exit code 2 (INCOMPLETE). This is covered by
`test_verify_catches_replay_against_different_sha`.

### Tamper attack

**Attack:** A contributor edits the receipt JSON after signing (e.g., changes
the AI level from `agent` to `assisted`).

**Mitigation:** The ed25519 signature covers the canonical serialization of
the in-toto Statement. Any edit breaks the signature OR the payload/statement
match (the embedded statement is re-canonicalized and compared to the
payload). This is covered by `test_verify_catches_edited_receipt`.

### Assertion-removal attack

**Attack:** A contributor removes a failing assertion to make tests pass.

**Mitigation:** The heuristics module reports removed assertions as a count
with file:line anchors. This is *informational* — it does not fail
verification, because removing an assertion is sometimes correct. The
reviewer sees the warning and decides. This is covered by
`test_verify_catches_assertion_removal_via_heuristics_warn`.

**Residual risk:** A contributor can replace an assertion with a weaker one
(`assert x == 1` → `assert x == 1 or True`). Workproof detects the removal of
the old assertion but not the weakening of the new one. Future versions could
parse the AST to compare assertion strength; see ROADMAP.

### Key theft

**Attack:** An attacker steals the contributor's private key and signs
fraudulent receipts.

**Mitigation:** v0.1 has no key rotation or revocation mechanism. If your key
is compromised, generate a new one (`workproof init --force`) and publish the
new public key. Old receipts remain valid (they were signed by the old key);
reviewers must be notified out-of-band to distrust the old key. Keyless
signing (sigstore) eliminates this class of attack entirely.

## Reporting a vulnerability

Email security@workproof.dev with a description and, if possible, a
minimal reproducer. We will acknowledge within 48 hours and aim for a fix
within 30 days. Public disclosure is coordinated with the reporter.

## What we will NOT do

- We will not add an LLM-based "is this PR suspicious" check. Determinism is
  the differentiation.
- We will not add a "code quality score" to receipts. That's a judgment, and
  judgments are for humans.
- We will not overclaim what receipts prove. If you find a claim in the docs
  that exceeds what the code actually verifies, that's a bug — please report
  it.
