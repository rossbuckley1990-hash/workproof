# Demo repo for Workproof

This is a tiny Python project used to demonstrate the Workproof workflow
end-to-end. See `docs/DEMO.md` in the parent directory for the script.

## What's here

- `calculator.py` — a trivial module with a bug
- `test_calculator.py` — a test that catches the bug
- `.workproof.yml` — Workproof policy (allows `pytest`)
- `.github/workflows/workproof.yml` — PR check that runs the Workproof Action

## The scripted PR

1. Base commit: `calculator.py` with `add(a, b)` returning `a - b` (bug), test fails.
2. "AI-assisted" fix: change `a - b` to `a + b`, test passes.
3. Contributor runs:
   - `workproof init`
   - `workproof run -- pytest`
   - `workproof attest --ai-level assisted --agent claude-code`
4. Receipt is committed at `.workproof/receipts/<head_sha>.json`.
5. PR is opened.
6. GitHub Action verifies the receipt and posts a ✓ sticky comment.
