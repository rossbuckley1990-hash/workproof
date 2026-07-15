"""Receipt verification: the heart of Workproof.

``verify_receipt`` is the single entry point. It returns a structured
:class:`VerificationResult` carrying exit-code semantics:

- ``exit_code == 0`` → verified (signature valid, hash chain intact, head SHA
  matches, declared commands are a subset of policy)
- ``exit_code == 1`` → tampered (signature invalid, hash chain broken, or
  payload/statement mismatch)
- ``exit_code == 2`` → incomplete (signature valid but the receipt references
  a tree / SHA / commands the verifier cannot reconcile against the repo)

This split lets the GitHub Action show ⚠ vs ✗ distinctly (D07 in DECISIONS.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from workproof.chain import ChainError, verify_chain
from workproof.policy import Policy
from workproof.receipt import Receipt, ReceiptError, parse_receipt, verify_receipt_signature

# Exit codes (per spec)
EXIT_VERIFIED = 0
EXIT_TAMPERED = 1
EXIT_INCOMPLETE = 2


@dataclass
class VerificationResult:
    """Structured outcome of receipt verification.

    ``checks`` is an ordered list of ``(name, status, detail)`` triples where
    status is one of ``"pass"``, ``"fail"``, ``"warn"``. The GitHub Action
    renders this as a table directly.
    """

    exit_code: int
    checks: list[tuple[str, str, str]] = field(default_factory=list)
    receipt: Receipt | None = None
    diagnosis: str = ""

    @property
    def verified(self) -> bool:
        return self.exit_code == EXIT_VERIFIED

    def add(self, name: str, status: str, detail: str = "") -> None:
        self.checks.append((name, status, detail))


def verify_receipt(
    receipt_dict: dict[str, Any],
    repo: Path | None = None,
    policy: Policy | None = None,
    expected_head_sha: str | None = None,
) -> VerificationResult:
    """Verify a Workproof receipt dict against the repo and policy.

    - ``receipt_dict``: parsed JSON dict of the receipt file.
    - ``repo``: path to the git repo to check the head SHA against. If
      ``None``, the SHA check is skipped (status ``warn``).
    - ``policy``: project policy. If ``None``, the command-subset check is
      skipped (status ``warn``).
    - ``expected_head_sha``: if provided, the receipt's head SHA must match.

    Returns a :class:`VerificationResult`. The caller is responsible for
    translating ``exit_code`` to a process exit code.
    """
    result = VerificationResult(exit_code=EXIT_VERIFIED)

    # ---- 1. Structural parse (incl. payload/statement match) ----
    try:
        receipt = parse_receipt(receipt_dict)
        result.receipt = receipt
        result.add("receipt_structure", "pass", "valid envelope, payload matches statement")
    except ReceiptError as e:
        result.add("receipt_structure", "fail", str(e))
        result.exit_code = EXIT_TAMPERED
        result.diagnosis = f"Receipt is tampered or malformed: {e}"
        return result

    # ---- 2. Signature ----
    if verify_receipt_signature(receipt):
        result.add("signature", "pass", "ed25519 signature valid")
    else:
        result.add("signature", "fail", "ed25519 signature does not verify")
        result.exit_code = EXIT_TAMPERED
        result.diagnosis = "Signature verification failed — receipt is forged or corrupted."
        return result

    # ---- 3. Hash chain integrity ----
    entries = receipt.statement.get("predicate", {}).get("entries", [])
    try:
        verify_chain(entries)
        result.add("hash_chain", "pass", f"{len(entries)} entries chain intact")
    except ChainError as e:
        result.add("hash_chain", "fail", str(e))
        result.exit_code = EXIT_TAMPERED
        result.diagnosis = f"Hash chain broken: {e}"
        return result

    # ---- 4. Head SHA match ----
    head_sha = _extract_head_sha(receipt.statement)
    if expected_head_sha is not None:
        if head_sha == expected_head_sha:
            result.add("head_sha", "pass", f"matches expected {head_sha[:12]}")
        else:
            result.add(
                "head_sha",
                "fail",
                f"receipt says {head_sha[:12]}, expected {expected_head_sha[:12]}",
            )
            result.exit_code = EXIT_INCOMPLETE
            result.diagnosis = (
                f"Receipt was issued for a different commit ({head_sha[:12]}) than the "
                f"current PR head ({expected_head_sha[:12]}). This is a replay or "
                f"cross-PR paste, not a valid receipt for this PR."
            )
            return result
    elif repo is not None:
        actual_sha = _git_head_sha(repo)
        if actual_sha and actual_sha == head_sha:
            result.add("head_sha", "pass", f"matches repo HEAD {head_sha[:12]}")
        elif actual_sha:
            result.add(
                "head_sha",
                "fail",
                f"receipt says {head_sha[:12]}, repo HEAD is {actual_sha[:12]}",
            )
            result.exit_code = EXIT_INCOMPLETE
            result.diagnosis = (
                f"Receipt was issued for commit {head_sha[:12]} but the repo is at "
                f"{actual_sha[:12]}. Check out the receipt's commit before verifying, "
                f"or pass --repo at the right SHA."
            )
            return result
        else:
            result.add("head_sha", "warn", "repo has no HEAD (empty git repo?)")
    else:
        result.add("head_sha", "warn", "no repo or expected_sha provided; skipping")

    # ---- 5. Command subset of policy ----
    if policy is None:
        result.add("command_policy", "warn", "no policy provided; skipping")
    else:
        declared = _declared_commands(receipt)
        violations = [c for c in declared if not policy.is_command_allowed(c)]
        if not violations:
            result.add(
                "command_policy",
                "pass",
                f"{len(declared)} command(s) all allowed by policy",
            )
        else:
            vlist = "; ".join(" ".join(v) for v in violations)
            result.add(
                "command_policy",
                "fail",
                f"{len(violations)} command(s) not in policy: {vlist}",
            )
            result.exit_code = EXIT_INCOMPLETE
            result.diagnosis = (
                f"Receipt records commands not allowed by project policy: {vlist}. "
                f"Either update .workproof.yml or reject this PR."
            )
            return result

    # ---- 6. Test files touched (informational) ----
    test_files = _test_files_touched(receipt)
    if test_files:
        result.add(
            "test_files_touched",
            "pass",
            f"{len(test_files)} test file(s): {', '.join(test_files[:3])}"
            + ("…" if len(test_files) > 3 else ""),
        )
    else:
        result.add("test_files_touched", "pass", "no test files touched")

    # ---- 7. Heuristics (informational; never fails verification) ----
    h = receipt.statement.get("predicate", {}).get("heuristics", {})
    ar = h.get("assertions_removed", 0)
    sm = h.get("new_skip_markers", 0)
    if ar == 0 and sm == 0:
        result.add("heuristics", "pass", "no test-weakening signals detected")
    else:
        detail = []
        if ar:
            detail.append(f"{ar} assertion(s) removed")
        if sm:
            detail.append(f"{sm} new skip/xfail marker(s)")
        result.add("heuristics", "warn", "; ".join(detail))

    # ---- 8. AI declaration (informational) ----
    ai = receipt.statement.get("predicate", {}).get("ai_level", "unknown")
    agent = receipt.statement.get("predicate", {}).get("agent", "unknown")
    if ai == "none":
        result.add("ai_declaration", "pass", "declared no AI use")
    else:
        result.add("ai_declaration", "pass", f"declared {ai} (agent: {agent})")

    if result.exit_code == EXIT_VERIFIED:
        result.diagnosis = "Receipt verified: signature valid, chain intact, head SHA matches."
    return result


# ----- helpers -----


def _extract_head_sha(statement: dict[str, Any]) -> str:
    for sub in statement.get("subject", []):
        digests = sub.get("digest", {})
        if "gitSha" in digests:
            return digests["gitSha"]
    return ""


def _git_head_sha(repo: Path) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _declared_commands(receipt: Receipt) -> list[list[str]]:
    """Extract the list of argvs recorded in the receipt's entries."""
    out: list[list[str]] = []
    for e in receipt.statement.get("predicate", {}).get("entries", []):
        argv = e.get("argv")
        if isinstance(argv, list):
            out.append([str(a) for a in argv])
    return out


def _test_files_touched(receipt: Receipt) -> list[str]:
    p = receipt.statement.get("predicate", {})
    return sorted(
        {
            *p.get("test_files_added", []),
            *p.get("test_files_modified", []),
            *p.get("test_files_deleted", []),
        }
    )
