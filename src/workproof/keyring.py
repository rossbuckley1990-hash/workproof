"""Keyring: load/store ed25519 keys under ``~/.workproof/``.

Format (D05 in DECISIONS.md):
- ``~/.workproof/id_ed25519``: 32-byte private seed, mode 0600.
- ``~/.workproof/id_ed25519.pub``: 32-byte public key, mode 0644, plus a
  trailing newline for human-readability (stripped on load).

No passphrase support in v0.1 — local attacker who owns the key already wins.
"""

from __future__ import annotations

import os
from pathlib import Path

from workproof.crypto import KeyPair, derive_public_key, generate_keypair

DEFAULT_KEYRING_DIR = Path.home() / ".workproof"
PRIVATE_KEY_PATH = DEFAULT_KEYRING_DIR / "id_ed25519"
PUBLIC_KEY_PATH = DEFAULT_KEYRING_DIR / "id_ed25519.pub"


class KeyringError(Exception):
    """Raised when the keyring is missing or malformed."""


def keyring_dir() -> Path:
    return DEFAULT_KEYRING_DIR


def has_keys() -> bool:
    return PRIVATE_KEY_PATH.exists() and PUBLIC_KEY_PATH.exists()


def generate_and_store(overwrite: bool = False) -> KeyPair:
    """Generate a new keypair and store it on disk.

    Raises :class:`KeyringError` if keys already exist and ``overwrite`` is
    False — this prevents clobbering an existing identity by accident.
    """
    if has_keys() and not overwrite:
        raise KeyringError(
            f"keys already exist at {PRIVATE_KEY_PATH}; pass overwrite=True to regenerate"
        )
    DEFAULT_KEYRING_DIR.mkdir(parents=True, exist_ok=True)
    kp = generate_keypair()
    # Write private key with 0600
    fd = os.open(str(PRIVATE_KEY_PATH), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.write(fd, kp.private_seed)
    finally:
        os.close(fd)
    # If the file already existed, force its mode (umask might have widened it)
    os.chmod(PRIVATE_KEY_PATH, 0o600)
    # Write public key
    PUBLIC_KEY_PATH.write_bytes(kp.public_key)
    os.chmod(PUBLIC_KEY_PATH, 0o644)
    return kp


def load() -> KeyPair:
    """Load the existing keypair from disk.

    Raises :class:`KeyringError` if keys are missing, and
    :class:`SigningKeyLoadError` if they are malformed.
    """
    if not has_keys():
        raise KeyringError(f"no keys found at {PRIVATE_KEY_PATH}; run `workproof init` first")
    seed = PRIVATE_KEY_PATH.read_bytes()
    pub = PUBLIC_KEY_PATH.read_bytes().rstrip(b"\n")
    # Verify the public key matches the seed; refuse to load mismatched keys.
    derived = derive_public_key(seed)
    if derived != pub:
        raise KeyringError(
            "public key does not match private seed — keyring is inconsistent; "
            "rerun `workproof init --force` to regenerate"
        )
    return KeyPair(private_seed=seed, public_key=pub)


def public_key_b64() -> str:
    """Return the base64-encoded public key for sharing/publishing."""
    import base64

    return base64.b64encode(load().public_key).decode("ascii")
