# Demo: verified AI-assisted PR in 60 seconds

This document describes how to record the demo GIF for Workproof. The GIF
shows: a contributor fixes a bug with AI assistance, records a receipt, opens
a PR, and the GitHub Action posts a verified ✓ comment — all in under 60
seconds.

## Prerequisites

- A fresh Linux/macOS machine with Python 3.11+ and `pipx`
- A GitHub account and a fork of `workproof/workproof`
- (For the GIF) a screen recorder like [peek](https://github.com/phw/peek) or
  [LICEcap](https://www.cockos.com/licecap/)

## Setup (one-time, off-screen)

```bash
pipx install workproof-cli
git clone https://github.com/<you>/workproof
cd workproof/examples/demo-repo
git checkout -b demo/fix-add-bug
```

## Script (record this)

| Time | Action | Command / screen |
|---|---|---|
| 0:00 | Show the bug | `python -c "from calculator import add; print(add(2,3))"` → prints `5` (already fixed in demo; for the GIF, start from the base commit where it returns `-1`) |
| 0:05 | Initialize Workproof | `workproof init` |
| 0:10 | Run the tests | `workproof run -- pytest` |
| 0:15 | Bundle the receipt | `workproof attest --ai-level assisted --agent claude-code` |
| 0:20 | Show the Markdown block | (output of `attest`) |
| 0:25 | Commit the receipt | `git add .workproof/receipts/ && git commit -m "fix: add() now adds"` |
| 0:30 | Push and open PR | `git push -u origin demo/fix-add-bug` then `gh pr create` |
| 0:35 | Show the PR description | paste the Markdown block |
| 0:40 | Show the Action running | (GitHub Actions tab) |
| 0:50 | Show the sticky comment | ✓ ✓ ✓ ✓ ✓ ✓ ✓ ✓ (8 checks green) |
| 0:55 | Show the commit status | "Workproof: success" |

## Recording tips

- Use a 1280x720 window; larger and the text is unreadable in a GIF.
- Hide your terminal prompt: `export PS1='$ '` for a clean look.
- Type commands into a text editor first, then paste — looks more deliberate.
- The `attest` output includes the `<!-- workproof:sticky:v1 -->` marker; mention
  that this is how the Action finds and updates the same comment on each push.

## What the reviewer sees

The sticky comment on the PR contains:

```
## Workproof verification
| Check | Status |
|---|---|
| Receipt | `.workproof/receipts/<sha>.json` (source: conventional) |
| Head SHA | `abc123def456` |
| Result | **VERIFIED** |
```

Plus a `<details>` block with the per-check table (signature, hash chain, head
SHA, command policy, test files, heuristics, AI declaration) and (if
`reexecute: true`) the re-executed command exit codes.

## What the reviewer does NOT see

- The actual stdout/stderr of the tests (only their sha256 + first 200 lines
  gzipped, stored in the receipt)
- Any LLM judgment about code quality
- Any "suspicion score" — heuristics are counts, never verdicts
