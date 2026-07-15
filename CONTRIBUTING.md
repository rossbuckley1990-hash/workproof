# Contributing to Workproof

## AI-assisted PRs require a Workproof receipt

This project eats its own dog food. If you use an AI assistant (Copilot,
Claude Code, Cursor, etc.) to help with a PR, you **must** attach a Workproof
receipt. PRs without a receipt will be labeled `needs-receipt` and will not be
reviewed until one is added.

### How to attach a receipt

```bash
# In your fork, on your PR branch:
pipx install workproof-cli
workproof init
workproof run -- pytest
workproof run -- ruff check .
workproof attest --ai-level assisted --agent <name>
git add .workproof/receipts/
git commit -m "chore: add workproof receipt"
git push
```

Then paste the Markdown block from `workproof attest` into your PR description.
The CI check will verify the receipt and post a sticky comment with the result.

### AI level taxonomy

When attesting, declare one of:

- `none` — you wrote every line yourself, no AI assistance.
- `assisted` — an AI assistant helped (autocomplete, chat suggestions), but you
  wrote and reviewed every line.
- `agent` — an AI agent wrote code autonomously; you reviewed and tested it.

Honest declaration is the entire point. Misrepresenting `agent` as `assisted`
(or `assisted` as `none`) defeats the system and will get your PR closed.

## Development setup

```bash
git clone https://github.com/workproof/workproof
cd workproof
python -m venv .venv && source .venv/bin/activate
pip install -e '.[dev]'  # editable install + ruff + pytest
pytest                   # all tests must pass
ruff check src tests     # must be clean
ruff format src tests    # must be clean
```

## Commit style

Conventional commits (`feat:`, `fix:`, `docs:`, `test:`, `chore:`). Small
commits. Each milestone in the build order is one commit.

## Adding a heuristic

If you add a new test-weakening heuristic, you **must** ship:

1. One true-positive fixture test (the heuristic fires on a real weakening).
2. One known-false-positive fixture test (the heuristic fires on a benign
   change), with the limitation documented in the test docstring.

Heuristics report *counts*, never verdicts. See
[`DECISIONS.md`](DECISIONS.md) D08 and D12.

## Security disclosures

See [`SECURITY.md`](SECURITY.md). The threat model is public by design —
overclaiming will get the project destroyed on Hacker News.
