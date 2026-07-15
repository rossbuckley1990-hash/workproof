# Workproof

Workproof was built with a coding agent in a day. Every PR to this repo
carries its own Workproof receipt, including the ones that built it. If the
idea can't survive that, it shouldn't exist.

**Deterministic evidence layer for AI-assisted pull requests.**

Maintainers are flooded with AI-generated PRs whose claims can't be verified.
Workproof is the DCO sign-off of the agent era: honest AI-assisted contributors
attach a signed receipt of the work they actually performed (commands run,
outputs, repo tree state); missing receipts become a triage signal.

**No LLM calls anywhere in this product.** Determinism is the differentiation.

## Install → receipt → verified PR in under 5 minutes

```bash
# 1. Install (one-time)
pipx install workproof-cli

# 2. Initialize keys + policy in your repo
cd your-repo
workproof init

# 3. Record evidence of your work (run against the commit you'll attest)
workproof run -- pytest
workproof run -- ruff check .

# 4. Commit your code changes FIRST (evidence must be recorded against the commit you attest)
git add -A && git commit -m "feat: add foo"

# 5. Re-run evidence against the committed code, then bundle a receipt
workproof run -- pytest
workproof attest --ai-level assisted --agent claude-code

# 6. Open a PR — paste the entire output of `attest` into the PR body.
#    The receipt JSON lives in the PR body, NOT in the git tree. This is the
#    anti-laundering contract: the attested commit IS the PR head.
```

If your repo has the Workproof GitHub Action installed (see
[`examples/demo-repo/.github/workflows/workproof.yml`](examples/demo-repo/.github/workflows/workproof.yml)),
the Action will verify your receipt and post a sticky comment with the result:

```
## Workproof verification
| Check | Status |
|---|---|
| Receipt | `.workproof/receipts/<sha>.json` |
| Head SHA | `abc123def456` |
| Result | **VERIFIED** |
```

## What a receipt proves

A Workproof receipt is a signed in-toto Statement that proves:

1. **Specific commands ran** against a specific git tree (HEAD SHA recorded).
2. **Specific outputs were produced** (sha256 of stdout/stderr; first 200 lines
   retained gzipped under `.workproof/evidence/`).
3. **The session is intact** — entries are hash-chained; tampering is detected.
4. **The AI level was declared** — `none`, `assisted`, or `agent`, plus the
   agent name.
5. **Test-weakening signals are reported** — counts of removed assertions and
   new skip/xfail markers, with file:line anchors (informational, never a
   verdict).

## What a receipt does NOT prove

Read [`SECURITY.md`](SECURITY.md) for the full threat model. The short version:

- **Not comprehension.** A receipt proves commands ran, not that the contributor
  understood the output.
- **Not code quality.** No LLM judgment, no score, no "suspicion".
- **Not absence of malice.** A local attacker holding the key can fabricate a
  session around doctored tests. Mitigations: CI re-execution mode, policy
  pinning, and future keyless signing (see [`ROADMAP.md`](ROADMAP.md)).

**Raising the cost of lying is the claim — not eliminating it.**

## Commands

```
workproof init [--force] [--test-command CMD] [--build-command CMD]
workproof run -- <cmd> [args...]
workproof attest --ai-level {none|assisted|agent} --agent <name> [--base SHA] [--head SHA]
workproof verify <receipt.json> [--repo PATH] [--policy PATH] [--expected-head-sha SHA]
workproof version
```

Exit codes for `verify`: `0` verified, `1` tampered, `2` incomplete.

## GitHub Action

```yaml
# .github/workflows/workproof.yml
name: Workproof
on:
  pull_request:
    branches: [main]
permissions:
  contents: read
  pull-requests: write  # post + update the sticky comment
  statuses: write       # set the Workproof commit status
jobs:
  verify:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - uses: workproof/workproof@v0.1
        with:
          reexecute: true           # re-run declared tests, compare exit codes
          fail-on-incomplete: true  # fail job if receipt doesn't match this PR
```

Inputs:

| Input | Default | Description |
|---|---|---|
| `receipt-path` | `""` | Explicit path to receipt JSON. If empty, searches conventional path + PR body. |
| `reexecute` | `false` | Re-execute declared test commands; compare exit codes only. |
| `fail-on-incomplete` | `true` | Fail the job on INCOMPLETE (exit 2). TAMPERED (exit 1) always fails. |
| `github-token` | `${{ github.token }}` | Token for posting the sticky comment + commit status. |

## FAQ (honest)

**Q: Can't a contributor just run `workproof run -- echo hi` and ignore the failing tests?**
A: Yes. The receipt records what ran, not what should have run. The reviewer
sees the recorded exit codes; if `pytest` isn't in the receipt, that's a signal.
Policy pinning (`.workproof.yml` `allowed_commands`) lets maintainers require
specific commands be present.

**Q: Can't a contributor edit the receipt after the fact?**
A: No — the ed25519 signature covers the canonical serialization of the
in-toto Statement. Any edit breaks the signature. (A local attacker holding the
private key can re-sign, which is why keyless signing is on the roadmap.)

**Q: Can't a contributor replay an old receipt on a new PR?**
A: No — the receipt's subject is the head SHA. The Action passes
`--expected-head-sha` so a receipt from a different commit is rejected with
exit code 2 (INCOMPLETE).

**Q: Why no LLM-based "is this PR suspicious" check?**
A: Because that would be non-deterministic, and determinism is the
differentiation. Two reviewers of the same receipt must reach the same
conclusion. An LLM judge would make Workproof just another opinion layer.

**Q: Does this catch every AI-generated PR?**
A: No. It catches PRs that *claim* to have run tests but didn't, PRs that
removed assertions, and PRs with mismatched receipts. It does not catch a
determined adversary who runs real tests, signs an honest receipt, and submits
bad code anyway. Workproof raises the cost of lying; it doesn't eliminate lying.

**Q: Why ed25519 and not sigstore/keyless?**
A: Ed25519 is simple, fast, and stdlib-friendly via PyNaCl. Keyless signing
(sigstore / Fulcio) is the plan for v0.2 — see [`ROADMAP.md`](ROADMAP.md). The
receipt schema is already an in-toto Statement, so the migration is mechanical.

## Project layout

```
src/workproof/        # ~1500 lines of core library
  canonical.py        # deterministic JSON + sha256
  chain.py            # hash-chained entries
  crypto.py           # ed25519 wrapper
  session.py          # append-only JSONL session log
  receipt.py          # in-toto Statement + DSSE envelope
  policy.py           # .workproof.yml (tiny YAML subset)
  runner.py           # subprocess exec + evidence recording
  keyring.py          # ~/.workproof/ key storage
  git_analysis.py     # diff name-status + test-file categorization
  heuristics.py       # assertion-removal + skip-marker detection
  attester.py         # bundle session + git + heuristics → signed receipt
  verifier.py         # 8-check verifier with 3-state exit codes
  cli.py              # Typer app: init / run / attest / verify
tests/                # ~100 tests, all green, ruff clean
action.yml            # composite GitHub Action
examples/demo-repo/   # scripted AI-assisted PR
docs/                 # SCHEMA.md, DEMO.md, SECURITY.md, ROADMAP.md
DECISIONS.md          # every non-obvious choice, with rationale
```

## License

MIT. See [`LICENSE`](LICENSE).

## Contributing

See [`CONTRIBUTING.md`](CONTRIBUTING.md). The one-line version: AI-assisted PRs
to this repo require a Workproof receipt.
