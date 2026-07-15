"""Ed25519 signing primitives.

Thin wrapper over PyNaCl. Keys are stored as the raw 32-byte seed (private)
and raw 32-byte public key. This is the OpenSSH ed25519 private-key wire
format (minus the framing), so future migration to agent-based signing is
mechanical.

Security note: v0.1 stores private keys unencrypted on disk. A local attacker
who can read ``~/.workproof/id_ed25519`` can sign arbitrary receipts. This is
acceptable for v0.1 because the threat model explicitly states that a local
attacker holding the key can fabricate sessions. Future keyless signing
(sigstore) is the real fix; see ROADMAP.md.
"""

from __future__ import annotations

from dataclasses import dataclass

from nacl import exceptions as nacl_exceptions
from nacl.signing import SigningKey, VerifyKey

SEED_BYTES = 32
PUBLIC_KEY_BYTES = 32


class SigningKeyLoadError(Exception):
    """Raised when a private key seed is malformed or the wrong length."""


@dataclass(frozen=True)
class KeyPair:
    """An ed25519 keypair.

    ``private_seed`` is the 32-byte secret seed. ``public_key`` is the 32-byte
    verification key derived from the seed.
    """

    private_seed: bytes
    public_key: bytes

    def __post_init__(self) -> None:
        if len(self.private_seed) != SEED_BYTES:
            raise SigningKeyLoadError(
                f"private seed must be {SEED_BYTES} bytes, got {len(self.private_seed)}"
            )
        if len(self.public_key) != PUBLIC_KEY_BYTES:
            raise SigningKeyLoadError(
                f"public key must be {PUBLIC_KEY_BYTES} bytes, got {len(self.public_key)}"
            )


def generate_keypair() -> KeyPair:
    """Generate a fresh ed25519 keypair."""
    sk = SigningKey.generate()
    return KeyPair(
        private_seed=bytes(sk.encode()[:SEED_BYTES]), public_key=bytes(sk.verify_key.encode())
    )


def _to_signing_key(private_seed: bytes) -> SigningKey:
    if len(private_seed) != SEED_BYTES:
        raise SigningKeyLoadError(
            f"private seed must be {SEED_BYTES} bytes, got {len(private_seed)}"
        )
    return SigningKey(private_seed)


def sign(private_seed: bytes, message: bytes) -> bytes:
    """Sign ``message`` with the ed25519 key derived from ``private_seed``.

    Returns the 64-byte detached signature.
    """
    sk = _to_signing_key(private_seed)
    return sk.sign(message).signature


def verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    """Verify a detached ed25519 signature.

    Returns ``True`` if valid, ``False`` otherwise. Never raises on bad
    signatures — the caller is a verifier and should treat all failures
    uniformly.
    """
    try:
        VerifyKey(public_key).verify(message, signature)
        return True
    except (nacl_exceptions.BadSignatureError, nacl_exceptions.CryptoError, ValueError):
        return False


def derive_public_key(private_seed: bytes) -> bytes:
    """Derive the 32-byte public key from a 32-byte private seed."""
    return bytes(_to_signing_key(private_seed).verify_key.encode())
