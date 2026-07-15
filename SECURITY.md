# SECURITY.md

## What Workproof proves

A Workproof receipt is a signed in-toto Statement that proves **specific
commands ran against a specific git tree and produced specific outputs**.

Concretely, a verified receipt means:

1. The contributor ran `workproof run -- <cmd>` and the command exited with
   the recorded exit code.
2. The command ran against a working tree whose HEAD SHA is recorded in the
   receipt; the dirty-diff sha256 is also recorded.
3. The stdout and stderr hashes match what was captured.
4. The session entries are hash-chained and the chain is intact.
5. The receipt was signed with an ed25519 key whose public half is embedded.
6. The declared AI level (none / assisted / agent) and agent name are
   attested under signature.
7. Counts of removed assertions and new skip/xfail markers are reported with
   file:line anchors.

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
