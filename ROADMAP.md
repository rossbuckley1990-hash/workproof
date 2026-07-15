# ROADMAP.md

Things Workproof will **not** do in v0.1, but might do later. If tempted to
add any of these to v0.1, add a line here instead.

## v0.2 — Keyless signing (sigstore)

The single most important change. Today, a contributor holds their own
ed25519 key; a local attacker who steals the key can sign anything. Keyless
signing via sigstore / Fulcio ties the signature to the contributor's GitHub
identity at sign time, so a stolen key is useless without the GitHub session.

The receipt schema is already an in-toto Statement, so the migration is
mechanical: the `signatures` array grows a `certificate` field and
`public_key` becomes optional.

## v0.3 — Sigstore bundle verification

Each receipt carries a full sigstore bundle (cert + signature + transparency
log entry) so verifiers don't need network access. The Workproof Action
verifies offline.

## v0.4 — Policy pinning v2

Today's `.workproof.yml` is a flat list of allowed commands. v0.4 adds:
- Per-command exit-code requirements (e.g., `pytest` must exit 0).
- Required-command sets (e.g., "must include either `pytest` or `tox`").
- Per-path policy (e.g., changes to `src/crypto/` require an additional
  reviewer's counter-signature).

## v0.5 — Multi-receipt aggregation

For PRs that span multiple sessions (e.g., a contributor ran tests on three
different machines), allow a PR to carry multiple receipts that aggregate
into a single verification result.

## v0.6 — AST-aware heuristics

Today's heuristics are regex-based and have documented false positives (see
`tests/test_heuristics.py`). v0.6 uses Python's `ast` module to:
- Distinguish `assert` statements from `assert` in strings/comments.
- Compare assertion *strength* (not just count) — detect
  `assert x == 1` → `assert x == 1 or True`.
- Detect `try/except` swallowing of assertion failures.

JS/TS heuristics would use a parser (e.g., `tree-sitter`).

## v0.7 — GitLab / Bitbucket / Gitea Actions

The composite action pattern is portable. The receipt format is
platform-agnostic. The only platform-specific code is the sticky-comment
posting, which has a clean abstraction boundary.

## v0.8 — Public key registry

A simple JSON file (mirrored to a CDN) mapping GitHub usernames to public
keys, so reviewers don't have to trust a key embedded in the receipt alone.
Keys are added via PR to the registry; keys are removed via PR + signed
revocation.

## Not on the roadmap (ever)

- **SaaS, dashboard, auth, billing.** Workproof is a CLI + Action. No
  backend. No accounts.
- **LLM-based judgment or code-quality scoring.** Determinism is the
  differentiation. An LLM judge would make Workproof just another opinion
  layer.
- **Agent-internal hooks.** Workproof records what *ran*, not what the agent
  *thought*. The agent's internal state is not Workproof's business.
- **Browser extension.** The PR description + sticky comment are the UI.
- **Files-read tracking.** Out of scope for v0.1; might be revisited if
  maintainers ask for it.
