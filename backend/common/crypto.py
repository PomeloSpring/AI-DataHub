"""AES-256-GCM encryption for datasource passwords."""

import os
import base64
import hashlib
import logging

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.common.config import ADH_SECRET_KEY

logger = logging.getLogger(__name__)


def _get_key() -> bytes:
    """Derive a 32-byte key from ADH_SECRET_KEY using SHA-256."""
    return hashlib.sha256(ADH_SECRET_KEY.encode()).digest()


def encrypt_password(plaintext: str) -> str:
    """Encrypt a password using AES-256-GCM.

    Args:
        plaintext: The password to encrypt.

    Returns:
        Base64-encoded string containing IV + ciphertext + GCM tag.
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    # 12 bytes random IV (recommended for GCM)
    iv = os.urandom(12)
    ct = aesgcm.encrypt(iv, plaintext.encode("utf-8"), None)
    # Format: base64(iv + ciphertext_with_tag)
    return base64.b64encode(iv + ct).decode("ascii")


def decrypt_password(encrypted: str) -> str:
    """Decrypt an AES-256-GCM encrypted password.

    Args:
        encrypted: Base64-encoded encrypted password string.

    Returns:
        Decrypted plaintext password.

    Raises:
        ValueError: If decryption fails (wrong key or corrupted data).
    """
    key = _get_key()
    aesgcm = AESGCM(key)
    data = base64.b64decode(encrypted)
    if len(data) < 29:  # 12 (IV) + 16 (GCM tag) + 1 (min ciphertext)
        raise ValueError("Invalid encrypted data: too short")
    iv = data[:12]
    ct = data[12:]
    try:
        return aesgcm.decrypt(iv, ct, None).decode("utf-8")
    except Exception as e:
        raise ValueError(f"Decryption failed: {e}")


def is_encrypted(password: str) -> bool:
    """Check if a password string appears to be encrypted (base64 format).

    This is a heuristic check used during migration to avoid double-encrypting.
    """
    if not password:
        return False
    try:
        data = base64.b64decode(password)
        # Encrypted data should be at least 12 (IV) + 16 (GCM tag) + 1 = 29 bytes
        return len(data) >= 29
    except Exception:
        return False
