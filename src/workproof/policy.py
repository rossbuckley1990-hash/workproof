"""Configuration: ``.workproof.yml`` project policy file.

The policy file declares the test/build commands the contributor is allowed
to record receipts against, plus the AI-level default for the project. It is
intentionally minimal — v0.1 is not a CI policy engine.

Format: a *tiny* YAML subset (flat keys, one list of strings, no nesting).
We do not depend on PyYAML because the spec mandates stdlib + pynacl + Typer
only. The parser is hand-rolled for our exact format and refuses anything
more complex; this keeps the attack surface tiny.

Example::

    # .workproof.yml
    policy_version: "0.1"
    allowed_commands:
      - pytest
      - ruff check .
      - python -m build
    default_ai_level: assisted
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

POLICY_VERSION = "0.1"
DEFAULT_POLICY_PATH = ".workproof.yml"


class PolicyError(Exception):
    """Raised when a policy file is malformed or missing required fields."""


@dataclass
class Policy:
    """Project policy loaded from ``.workproof.yml``."""

    policy_version: str = POLICY_VERSION
    allowed_commands: list[str] = field(default_factory=list)
    default_ai_level: str = "assisted"

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Policy:
        if "policy_version" not in d:
            raise PolicyError("missing policy_version")
        if d["policy_version"] != POLICY_VERSION:
            raise PolicyError(
                f"unsupported policy_version {d['policy_version']!r}; expected {POLICY_VERSION!r}"
            )
        allowed = d.get("allowed_commands", [])
        if not isinstance(allowed, list):
            raise PolicyError("allowed_commands must be a list of strings")
        return cls(
            policy_version=d["policy_version"],
            allowed_commands=[str(c) for c in allowed],
            default_ai_level=str(d.get("default_ai_level", "assisted")),
        )

    @classmethod
    def load(cls, path: str | Path = DEFAULT_POLICY_PATH) -> Policy:
        p = Path(path)
        if not p.exists():
            raise PolicyError(f"policy file not found: {path}")
        return cls.from_dict(_parse_tiny_yaml(p.read_text(encoding="utf-8")))

    def to_dict(self) -> dict[str, Any]:
        return {
            "policy_version": self.policy_version,
            "allowed_commands": self.allowed_commands,
            "default_ai_level": self.default_ai_level,
        }

    def save(self, path: str | Path = DEFAULT_POLICY_PATH) -> None:
        Path(path).write_text(_emit_tiny_yaml(self.to_dict()), encoding="utf-8")

    def is_command_allowed(self, argv: list[str]) -> bool:
        """Return True iff ``argv`` is a prefix- or exact-match of an allowed command.

        Open policy (empty ``allowed_commands``) allows everything — useful for
        projects that haven't pinned commands yet. Closed policy (non-empty)
        requires every recorded command to match.
        """
        if not self.allowed_commands:
            return True
        cmd_str = " ".join(argv)
        for allowed in self.allowed_commands:
            allowed_parts = allowed.split()
            if argv == allowed_parts:
                return True
            if len(argv) >= len(allowed_parts) and argv[: len(allowed_parts)] == allowed_parts:
                return True
            if cmd_str == allowed:
                return True
        return False


# ----- tiny YAML subset parser/emitter -----


def _strip_quotes(s: str) -> str:
    s = s.strip()
    if len(s) >= 2 and s[0] == s[-1] and s[0] in ("'", '"'):
        return s[1:-1]
    return s


def _parse_tiny_yaml(text: str) -> dict[str, Any]:
    """Parse a *very* small YAML subset.

    Supports:
    - ``#`` comments
    - top-level ``key: value`` pairs (value may be quoted or bare)
    - one level of list under a key, items prefixed with ``- ``

    Anything else raises PolicyError. This is deliberate: the policy file is
    a security-relevant input and we want predictable parsing.
    """
    out: dict[str, Any] = {}
    current_list_key: str | None = None
    for lineno, raw in enumerate(text.splitlines(), start=1):
        line = raw.rstrip()
        # strip comments (only when # is at start of token or after whitespace)
        if "#" in line:
            # Find # not inside quotes
            in_q: str | None = None
            cut = len(line)
            for i, c in enumerate(line):
                if c in ("'", '"'):
                    in_q = c if in_q is None else None
                elif c == "#" and in_q is None and (i == 0 or line[i - 1] in " \t"):
                    cut = i
                    break
            line = line[:cut].rstrip()
        if not line.strip():
            continue
        if line.startswith(" ") or line.startswith("\t"):
            # List item
            stripped = line.strip()
            if not stripped.startswith("- "):
                raise PolicyError(f"line {lineno}: unexpected indented content: {raw!r}")
            if current_list_key is None:
                raise PolicyError(f"line {lineno}: list item without preceding key")
            item = _strip_quotes(stripped[2:])
            out.setdefault(current_list_key, []).append(item)
            continue
        # Top-level key
        if ":" not in line:
            raise PolicyError(f"line {lineno}: not a key:value pair: {raw!r}")
        key, _, val = line.partition(":")
        key = key.strip()
        val = val.strip()
        if val == "":
            # Could be start of a list block
            current_list_key = key
            out[key] = []
        else:
            current_list_key = None
            out[key] = _strip_quotes(val)
    return out


def _emit_tiny_yaml(d: dict[str, Any]) -> str:
    """Emit a tiny YAML subset matching what _parse_tiny_yaml accepts."""
    lines: list[str] = []
    for k, v in d.items():
        if isinstance(v, list):
            lines.append(f"{k}:")
            for item in v:
                # Quote if contains special chars, else bare
                if any(c in item for c in ":#\"'"):
                    lines.append(f'  - "{item}"')
                else:
                    lines.append(f"  - {item}")
        elif isinstance(v, str):
            if any(c in v for c in ":#\"' "):
                lines.append(f'{k}: "{v}"')
            else:
                lines.append(f"{k}: {v}")
        else:
            lines.append(f"{k}: {v}")
    return "\n".join(lines) + "\n"
