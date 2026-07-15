"""Command runner: executes a command and records an evidence entry.

Each entry captures:
- argv (the command as a list)
- repo-relative cwd
- git HEAD SHA + sha256 of the dirty diff (working-tree changes vs HEAD)
- start/end ISO timestamps (UTC, second precision)
- exit code
- sha256 of stdout and stderr (full bytes hashed; only first 200 lines
  retained gzipped under .workproof/evidence/<entry-id>.{out,err}.gz)
- environment fingerprint (OS, Python version, declared tool versions)

The entry is appended to the session log (hash-chained) and the evidence
blobs are written alongside.
"""

from __future__ import annotations

import contextlib
import gzip
import os
import platform
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from workproof.canonical import sha256_bytes
from workproof.session import Session

EVIDENCE_DIR = ".workproof/evidence"
MAX_RETAINED_LINES = 200


def utc_now_iso() -> str:
    """Return current UTC time as ISO 8601 with second precision and 'Z' suffix."""
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _git(repo: Path, *args: str) -> str:
    """Run a git command, return stdout stripped. Returns '' if git fails."""
    try:
        out = subprocess.run(
            ["git", *args],
            cwd=str(repo),
            capture_output=True,
            text=True,
            check=False,
            timeout=30,
        )
        return out.stdout.strip()
    except (subprocess.SubprocessError, FileNotFoundError):
        return ""


def get_head_sha(repo: Path) -> str:
    return _git(repo, "rev-parse", "HEAD")


def get_working_tree_hash(repo: Path) -> str:
    """Return the git tree hash of the current working tree state.

    Uses ``git write-tree`` via a TEMP index file so the user's real index is
    never mutated. The tree hash captures the exact state of the working tree
    (including uncommitted edits) at the moment the command ran.

    At verify time, this is compared to ``git rev-parse <subject_sha>^{tree}`` —
    if they match, the evidence was recorded against the same tree state as the
    attested commit. This replaces the old dirty-diff check, which incorrectly
    required a clean tree (forcing contributors to commit before testing).
    """
    git_dir = repo / ".git"
    if not git_dir.exists():
        return ""
    tmp_index = str(git_dir / "workproof-tmp-index")
    env = {**os.environ, "GIT_INDEX_FILE": tmp_index}
    try:
        # Load HEAD as base, then stage working tree changes, then write tree.
        _git_env(repo, env, "read-tree", "HEAD")
        _git_env(repo, env, "add", "-A")
        return _git_env(repo, env, "write-tree")
    except Exception:
        return ""
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_index)


def _git_env(repo: Path, env: dict, *args: str) -> str:
    """Run a git command with a custom environment."""
    out = subprocess.run(
        ["git", *args],
        cwd=str(repo),
        capture_output=True,
        text=True,
        check=True,
        timeout=30,
        env=env,
    )
    return out.stdout.strip()


def environment_fingerprint(declared_tools: dict[str, str] | None = None) -> dict[str, Any]:
    """Capture OS, Python version, and (optionally) declared tool versions.

    Per D06, this is metadata for human reviewers — never a verification
    target. Tools we look up automatically: git, python. Additional tools can
    be passed in (e.g. ``{"ruff": "ruff --version"}``).
    """
    tools: dict[str, str] = {}
    tools["python"] = sys.version.split()[0]
    tools["git"] = _git(Path.cwd(), "--version") or "unknown"
    if declared_tools:
        for name, cmd in declared_tools.items():
            try:
                parts = cmd.split()
                out = subprocess.run(parts, capture_output=True, text=True, check=False, timeout=10)
                tools[name] = (
                    (out.stdout or out.stderr).strip().splitlines()[0]
                    if (out.stdout or out.stderr)
                    else "unknown"
                )
            except (subprocess.SubprocessError, FileNotFoundError, IndexError):
                tools[name] = "unknown"
    return {
        "os": platform.platform(),
        "python": sys.version.split()[0],
        "tools": tools,
    }


def _retained_lines(data: bytes) -> bytes:
    """Return the first MAX_RETAINED_LINES lines of ``data`` as bytes."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines(keepends=True)[:MAX_RETAINED_LINES]
    return "".join(lines).encode("utf-8")


def _write_evidence(entry_id: str, kind: str, data: bytes) -> str:
    """Gzip the retained prefix of ``data`` to evidence dir; return relative path."""
    out_dir = Path(EVIDENCE_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    rel = f"{EVIDENCE_DIR}/{entry_id}.{kind}.gz"
    payload = _retained_lines(data)
    with gzip.open(rel, "wb") as f:
        f.write(payload)
    return rel


def run_and_record(
    argv: list[str],
    session: Session,
    repo: Path | None = None,
    env_tools: dict[str, str] | None = None,
) -> dict[str, Any]:
    """Execute ``argv`` and append an evidence entry to ``session``.

    Returns the recorded entry (including ``hash`` and ``prev_hash``).

    The subprocess inherits the parent environment. stdout and stderr are
    captured separately. Exit codes are propagated unchanged; this function
    never raises on non-zero exit (a failing test is still valid evidence).
    """
    repo = repo or Path.cwd()
    started = utc_now_iso()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(repo),
            capture_output=True,
            check=False,
            timeout=3600,
        )
        exit_code = proc.returncode
        stdout_b = proc.stdout
        stderr_b = proc.stderr
    except FileNotFoundError as e:
        # Command not found — record as evidence with a synthetic non-zero exit
        ended = utc_now_iso()
        entry_data = _build_entry(
            argv=argv,
            repo=repo,
            started=started,
            ended=ended,
            exit_code=127,
            stdout_b=b"",
            stderr_b=str(e).encode("utf-8"),
            env_fingerprint=environment_fingerprint(env_tools),
            evidence_paths={},
        )
        return session.append(entry_data)
    except subprocess.TimeoutExpired as e:
        ended = utc_now_iso()
        entry_data = _build_entry(
            argv=argv,
            repo=repo,
            started=started,
            ended=ended,
            exit_code=124,  # standard timeout exit code
            stdout_b=e.stdout or b"",
            stderr_b=(e.stderr or b"") + b"\n[workproof: timeout after 3600s]",
            env_fingerprint=environment_fingerprint(env_tools),
            evidence_paths={},
        )
        return session.append(entry_data)

    ended = utc_now_iso()

    # Write evidence blobs and record their relative paths
    entry_id = (
        f"{started.replace(':', '').replace('-', '')}_{abs(hash(tuple(argv))) & 0xFFFFFFFF:08x}"
    )
    evidence_paths: dict[str, str] = {}
    if stdout_b:
        evidence_paths["stdout"] = _write_evidence(entry_id, "out", stdout_b)
    if stderr_b:
        evidence_paths["stderr"] = _write_evidence(entry_id, "err", stderr_b)

    entry_data = _build_entry(
        argv=argv,
        repo=repo,
        started=started,
        ended=ended,
        exit_code=exit_code,
        stdout_b=stdout_b,
        stderr_b=stderr_b,
        env_fingerprint=environment_fingerprint(env_tools),
        evidence_paths=evidence_paths,
    )
    return session.append(entry_data)


def _build_entry(
    *,
    argv: list[str],
    repo: Path,
    started: str,
    ended: str,
    exit_code: int,
    stdout_b: bytes,
    stderr_b: bytes,
    env_fingerprint: dict[str, Any],
    evidence_paths: dict[str, str],
) -> dict[str, Any]:
    """Construct the entry dict (without hash/prev_hash — Session.append adds those)."""
    head_sha = get_head_sha(repo)
    tree_hash = get_working_tree_hash(repo)
    cwd_rel = _relative_cwd(repo)
    return {
        "kind": "command",
        "argv": argv,
        "cwd_relative": cwd_rel,
        "git": {
            "head_sha": head_sha,
            "tree_hash": tree_hash,
        },
        "started_at": started,
        "ended_at": ended,
        "exit_code": exit_code,
        "stdout_sha256": sha256_bytes(stdout_b),
        "stderr_sha256": sha256_bytes(stderr_b),
        "evidence_paths": evidence_paths,
        "environment_fingerprint": env_fingerprint,
    }


def _relative_cwd(repo: Path) -> str:
    """Return cwd relative to repo root, or '.' if at root."""
    try:
        cwd = Path.cwd()
        rel = cwd.relative_to(repo)
        return str(rel) if str(rel) != "." else "."
    except ValueError:
        return str(Path.cwd())
