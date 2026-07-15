"""Tests for ed25519 signing / verification primitives.

Written BEFORE implementation. Crypto layer must be a thin wrapper over pynacl
so behavior is predictable and auditable.
"""

from __future__ import annotations

import pytest

from workproof.crypto import (
    KeyPair,
    SigningKeyLoadError,
    derive_public_key,
    generate_keypair,
    sign,
    verify,
)


class TestCrypto:
    def test_generated_keypair_round_trips(self) -> None:
        kp = generate_keypair()
        msg = b"workproof test message"
        sig = sign(kp.private_seed, msg)
        assert verify(kp.public_key, msg, sig) is True

    def test_generated_public_key_is_32_bytes(self) -> None:
        assert len(generate_keypair().public_key) == 32

    def test_generated_private_seed_is_32_bytes(self) -> None:
        # We store the 32-byte seed (OpenSSH-compatible), not the 64-byte expanded form.
        assert len(generate_keypair().private_seed) == 32

    def test_signature_changes_with_message(self) -> None:
        kp = generate_keypair()
        assert sign(kp.private_seed, b"msg1") != sign(kp.private_seed, b"msg2")

    def test_signature_verifies_only_with_correct_message(self) -> None:
        kp = generate_keypair()
        sig = sign(kp.private_seed, b"original")
        assert verify(kp.public_key, b"original", sig) is True
        assert verify(kp.public_key, b"tampered", sig) is False

    def test_signature_verifies_only_with_correct_key(self) -> None:
        kp1 = generate_keypair()
        kp2 = generate_keypair()
        sig = sign(kp1.private_seed, b"msg")
        assert verify(kp1.public_key, b"msg", sig) is True
        assert verify(kp2.public_key, b"msg", sig) is False

    def test_empty_message_signs_and_verifies(self) -> None:
        kp = generate_keypair()
        sig = sign(kp.private_seed, b"")
        assert verify(kp.public_key, b"", sig) is True

    def test_load_corrupt_private_key_raises(self) -> None:
        with pytest.raises(SigningKeyLoadError):
            # 31 bytes instead of 32
            sign(b"\x00" * 31, b"msg")

    def test_derive_public_key_matches_generated(self) -> None:
        kp = generate_keypair()
        assert derive_public_key(kp.private_seed) == kp.public_key

    def test_keypair_rejects_wrong_lengths(self) -> None:
        with pytest.raises(SigningKeyLoadError):
            KeyPair(private_seed=b"\x00" * 31, public_key=b"\x00" * 32)
        with pytest.raises(SigningKeyLoadError):
            KeyPair(private_seed=b"\x00" * 32, public_key=b"\x00" * 31)
