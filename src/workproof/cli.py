"""Workproof CLI.

Four commands: ``init``, ``run``, ``attest``, ``verify``. Built on Typer.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path
from typing import Annotated

import typer

from workproof import __version__
from workproof.attester import AttestError
from workproof.attester import attest as build_and_sign_receipt
from workproof.keyring import (
    KeyringError,
    generate_and_store,
    has_keys,
    keyring_dir,
    load,
    public_key_b64,
)
from workproof.policy import DEFAULT_POLICY_PATH, Policy, PolicyError
from workproof.runner import run_and_record
from workproof.session import DEFAULT_SESSION_PATH, Session
from workproof.verifier import (
    EXIT_INCOMPLETE,
    EXIT_TAMPERED,
    EXIT_VERIFIED,
    VerificationResult,
    verify_receipt,
)

app = typer.Typer(
    name="workproof",
    help="Deterministic evidence layer for AI-assisted pull requests.",
    no_args_is_help=True,
    context_settings={"help_option_names": ["-h", "--help"]},
)


@app.command()
def version() -> None:
    """Print the Workproof version and exit."""
    typer.echo(__version__)


@app.command()
def init(
    force: Annotated[
        bool,
        typer.Option(
            "--force", help="Overwrite an existing keypair (DANGEROUS: changes your identity)."
        ),
    ] = False,
    test_command: Annotated[
        str,
        typer.Option(
            "--test-command", help="Initial allowed test command to record in .workproof.yml."
        ),
    ] = "pytest",
    build_command: Annotated[
        str,
        typer.Option("--build-command", help="Initial allowed build command (empty for none)."),
    ] = "",
) -> None:
    """Create ``.workproof.yml`` and generate an ed25519 keypair.

    Idempotent: refuses to overwrite an existing keypair unless ``--force``
    is given. The session file (``.workproof/session.jsonl``) is reset.
    """
    # 1. Policy file
    policy_path = Path(DEFAULT_POLICY_PATH)
    if policy_path.exists() and not force:
        typer.echo(f"  ✓ {policy_path} already exists (keeping)")
    else:
        allowed: list[str] = []
        if test_command:
            allowed.append(test_command)
        if build_command:
            allowed.append(build_command)
        Policy(allowed_commands=allowed, default_ai_level="assisted").save(policy_path)
        typer.echo(f"  ✓ wrote {policy_path}")

    # 2. Keys
    try:
        if has_keys() and not force:
            typer.echo(f"  ✓ keys already exist at {keyring_dir()}/ (keeping)")
        else:
            kp = generate_and_store(overwrite=force)
            typer.echo(f"  ✓ generated ed25519 keypair at {keyring_dir()}/")
            typer.echo(
                f"    public key (base64): {base64.b64encode(kp.public_key).decode('ascii')}"
            )
    except KeyringError as e:
        typer.echo(f"  ✗ key error: {e}", err=True)
        raise typer.Exit(1) from e

    # 3. Reset session
    Session(DEFAULT_SESSION_PATH).reset()
    typer.echo(f"  ✓ session reset ({DEFAULT_SESSION_PATH})")

    # 4. Instructions
    typer.echo("")
    typer.echo("Next steps:")
    typer.echo("  1. Publish your public key so reviewers can verify receipts:")
    typer.echo(
        f"       echo '{public_key_b64() if has_keys() else '<base64-pubkey>'}' > .workproof/pubkey.b64"
    )
    typer.echo("  2. Record evidence: workproof run -- pytest")
    typer.echo("  3. Bundle a receipt: workproof attest --ai-level assisted --agent <name>")


@app.command(
    context_settings={"allow_extra_args": True, "ignore_unknown_options": True},
)
def run(
    ctx: Annotated[typer.Context, typer.Option()],
    capture: Annotated[
        bool,
        typer.Option("--no-capture/--capture", help="Capture stdout/stderr (default on)."),
    ] = True,
) -> None:
    """Execute a command and record an evidence entry in the session log.

    Usage: ``workproof run -- pytest -x``. Everything after ``--`` is the
    command to run (argv), passed through verbatim — no shell, no globbing.
    """
    if not ctx.args:
        typer.echo("error: no command given. Usage: workproof run -- <cmd> [args...]", err=True)
        raise typer.Exit(2)
    argv = list(ctx.args)
    session = Session(DEFAULT_SESSION_PATH)
    if not Path(DEFAULT_POLICY_PATH).exists():
        typer.echo(
            f"warning: {DEFAULT_POLICY_PATH} not found; commands won't be policy-checked at attest time",
            err=True,
        )
    try:
        entry = run_and_record(argv=argv, session=session, repo=Path.cwd())
    except Exception as e:
        typer.echo(f"error: failed to run {argv}: {e}", err=True)
        raise typer.Exit(1) from e

    typer.echo(f"  ✓ recorded entry (hash {entry['hash'][:12]})")
    typer.echo(f"    argv: {' '.join(argv)}")
    typer.echo(f"    exit: {entry['exit_code']}")
    typer.echo(
        f"    head: {entry['git']['head_sha'][:12] if entry['git']['head_sha'] else '(no git)'}"
    )
    # Propagate the command's exit code so `workproof run` is scriptable
    raise typer.Exit(code=int(entry["exit_code"]))


@app.command()
def attest(
    ai_level: Annotated[
        str,
        typer.Option("--ai-level", help="none | assisted | agent"),
    ] = "assisted",
    agent: Annotated[
        str,
        typer.Option("--agent", help="Name of the AI agent used (e.g. claude-code)."),
    ] = "unknown",
    base: Annotated[
        str | None,
        typer.Option("--base", help="Base git SHA. Default: HEAD~1."),
    ] = None,
    head: Annotated[
        str | None,
        typer.Option("--head", help="Head git SHA. Default: HEAD."),
    ] = None,
    no_write: Annotated[
        bool,
        typer.Option("--no-write", help="Print the receipt to stdout; don't write a file."),
    ] = False,
) -> None:
    """Bundle the session into a signed receipt and write it under ``.workproof/receipts/``.

    Prints a Markdown summary suitable for pasting into a PR description.
    """
    # Resolve SHAs
    repo = Path.cwd()
    base_sha = base or _git_sha(repo, "HEAD~1")
    head_sha = head or _git_sha(repo, "HEAD")
    if not head_sha:
        typer.echo("error: not a git repo (no HEAD)", err=True)
        raise typer.Exit(1)
    if not base_sha:
        typer.echo("error: no base SHA and HEAD~1 doesn't exist; pass --base explicitly", err=True)
        raise typer.Exit(1)

    # Load session
    session = Session(DEFAULT_SESSION_PATH)
    if not session.exists():
        typer.echo(
            f"error: no session at {DEFAULT_SESSION_PATH}; run `workproof run -- <cmd>` first",
            err=True,
        )
        raise typer.Exit(1) from None

    # Load keys
    try:
        kp = load()
    except KeyringError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    # Load policy (optional — warn if missing)
    policy_dict: dict | None = None
    try:
        policy_dict = Policy.load().to_dict()
    except PolicyError:
        typer.echo(
            f"warning: {DEFAULT_POLICY_PATH} missing or malformed; receipt will record no policy",
            err=True,
        )

    try:
        receipt = build_and_sign_receipt(
            session=session,
            base_sha=base_sha,
            head_sha=head_sha,
            repo=repo,
            ai_level=ai_level,
            agent=agent,
            keypair=kp,
            policy_dict=policy_dict,
            write=not no_write,
        )
    except AttestError as e:
        typer.echo(f"error: {e}", err=True)
        raise typer.Exit(1) from e

    receipt_path = getattr(receipt, "receipt_path", None)
    typer.echo(receipt.to_markdown(receipt_path=receipt_path))


@app.command()
def verify(
    receipt: Annotated[
        Path,
        typer.Argument(
            help="Path to the receipt JSON file to verify.", exists=True, dir_okay=False
        ),
    ],
    repo: Annotated[
        Path | None,
        typer.Option("--repo", help="Path to the git repo to check the head SHA against."),
    ] = None,
    policy: Annotated[
        Path | None,
        typer.Option("--policy", help="Path to .workproof.yml for command-subset check."),
    ] = None,
    expected_head_sha: Annotated[
        str | None,
        typer.Option(
            "--expected-head-sha", help="Pin the head SHA the receipt must match (e.g. PR HEAD)."
        ),
    ] = None,
    allow_ancestor: Annotated[
        bool,
        typer.Option(
            "--allow-ancestor",
            help="Accept a receipt whose head_sha is an ancestor of the repo HEAD / expected SHA. "
            "Use when the receipt is committed in a separate commit on top of the code commit.",
        ),
    ] = False,
) -> None:
    """Verify a receipt against the repo and policy.

    Exit codes: 0 verified, 1 tampered, 2 incomplete (per spec).
    """
    receipt_dict = json.loads(receipt.read_text(encoding="utf-8"))

    policy_obj: Policy | None = None
    if policy is not None:
        try:
            policy_obj = Policy.load(policy)
        except PolicyError as e:
            typer.echo(f"error: bad policy: {e}", err=True)
            raise typer.Exit(2) from e

    result = verify_receipt(
        receipt_dict=receipt_dict,
        repo=repo,
        policy=policy_obj,
        expected_head_sha=expected_head_sha,
        allow_ancestor=allow_ancestor,
    )
    _print_verification_result(result)
    raise typer.Exit(code=result.exit_code)


@app.command()
def status() -> None:
    """Print the current Workproof state: keys, policy, session, receipts.

    Useful as a pre-flight check before ``attest`` — confirms that init was
    run, that there's a session to attest, and shows how many receipts exist.
    """
    from workproof.keyring import has_keys

    # Keys
    if has_keys():
        typer.echo(f"  ✓ keys present at {keyring_dir()}/")
    else:
        typer.echo(f"  ✗ no keys at {keyring_dir()}/ (run `workproof init`)")

    # Policy
    policy_path = Path(DEFAULT_POLICY_PATH)
    if policy_path.exists():
        try:
            p = Policy.load(policy_path)
            typer.echo(f"  ✓ policy: {policy_path} ({len(p.allowed_commands)} allowed command(s))")
        except PolicyError as e:
            typer.echo(f"  ⚠ policy at {policy_path} is malformed: {e}")
    else:
        typer.echo(f"  ✗ no policy at {policy_path} (run `workproof init`)")

    # Session
    session = Session(DEFAULT_SESSION_PATH)
    if session.exists():
        entries = session.entries()
        typer.echo(f"  ✓ session: {len(entries)} entry/entries at {DEFAULT_SESSION_PATH}")
        if entries:
            typer.echo(f"    last hash: {entries[-1].get('hash', '?')[:12]}")
    else:
        typer.echo(f"  ✗ no session at {DEFAULT_SESSION_PATH} (run `workproof run -- <cmd>`)")

    # Receipts
    receipts_dir = Path(".workproof/receipts")
    if receipts_dir.exists():
        receipts = list(receipts_dir.glob("*.json"))
        typer.echo(f"  ✓ receipts: {len(receipts)} at {receipts_dir}/")
    else:
        typer.echo(f"  • no receipts yet at {receipts_dir}/")


# ----- helpers -----


def _git_sha(repo: Path, ref: str) -> str:
    import subprocess

    try:
        out = subprocess.run(
            ["git", "rev-parse", ref],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=10,
        )
        return out.stdout.strip() if out.returncode == 0 else ""
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def _print_verification_result(result: VerificationResult) -> None:
    """Render a VerificationResult to stdout in a human-friendly table."""
    icon = {"pass": "✓", "fail": "✗", "warn": "⚠"}
    typer.echo("")
    typer.echo("Workproof verification")
    typer.echo("─────────────────────────────────────────────────────────────")
    for name, status, detail in result.checks:
        typer.echo(f"  {icon.get(status, '?')} {name:24s}  {detail}")
    typer.echo("─────────────────────────────────────────────────────────────")
    label = {
        EXIT_VERIFIED: "VERIFIED",
        EXIT_TAMPERED: "TAMPERED",
        EXIT_INCOMPLETE: "INCOMPLETE",
    }.get(result.exit_code, f"exit {result.exit_code}")
    typer.echo(f"  result: {label}")
    if result.diagnosis:
        typer.echo(f"  diagnosis: {result.diagnosis}")
    typer.echo("")


if __name__ == "__main__":
    app()
