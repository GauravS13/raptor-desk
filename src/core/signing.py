"""Signing and hashing for verifiable records.

What a signature proves: the payload was issued by this Raptor Desk
deployment and has not been altered since. The deployment holds one Ed25519
key, created on first boot inside the data volume and never committed.
Judges do not hold private keys; custodial per-judge keys would prove nothing
extra.

Everything signed or hashed is first serialised as canonical JSON, so any
verifier (including the stdlib-only ``tools/verify.py``) reproduces the exact
same bytes.
"""

import hashlib
import json
import os
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Any

from django.conf import settings
from nacl.encoding import HexEncoder
from nacl.exceptions import BadSignatureError
from nacl.signing import SigningKey, VerifyKey

ALGORITHM = "Ed25519"
GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8. The byte form everything is hashed and signed over."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def payload_hash(obj: Any) -> str:
    return sha256_hex(canonical_json(obj))


def chain_hash(previous_hash: str, current_payload_hash: str) -> str:
    """Link an entry to its predecessor, so editing any entry breaks every later hash."""
    return sha256_hex(f"{previous_hash}:{current_payload_hash}".encode())


def merkle_root(leaves: list[str]) -> str:
    """Root over hex leaf hashes. An odd node is paired with itself. Empty input hashes b""."""
    if not leaves:
        return sha256_hex(b"")
    level = list(leaves)
    while len(level) > 1:
        if len(level) % 2:
            level.append(level[-1])
        level = [
            sha256_hex(f"{a}{b}".encode()) for a, b in zip(level[::2], level[1::2], strict=True)
        ]
    return level[0]


@dataclass(frozen=True)
class Signature:
    algorithm: str
    key_id: str
    public_key: str
    value: str

    def as_dict(self) -> dict[str, str]:
        return {
            "alg": self.algorithm,
            "key_id": self.key_id,
            "public_key": self.public_key,
            "signature": self.value,
        }


def _key_path() -> Path:
    return Path(settings.DATA_DIR) / "keys" / "ed25519.seed"


@cache
def signing_key() -> SigningKey:
    """Load the deployment key, creating it atomically on first use."""
    path = _key_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return SigningKey(path.read_text(encoding="ascii").strip(), encoder=HexEncoder)
    key = SigningKey.generate()
    with os.fdopen(fd, "w", encoding="ascii") as handle:
        handle.write(key.encode(encoder=HexEncoder).decode("ascii"))
    return key


def public_key_hex() -> str:
    return signing_key().verify_key.encode(encoder=HexEncoder).decode("ascii")


def key_id(public_key: str | None = None) -> str:
    return sha256_hex(bytes.fromhex(public_key or public_key_hex()))[:16]


def sign(obj: Any) -> Signature:
    signed = signing_key().sign(canonical_json(obj))
    return Signature(
        algorithm=ALGORITHM,
        key_id=key_id(),
        public_key=public_key_hex(),
        value=signed.signature.hex(),
    )


def verify(obj: Any, signature_hex: str, public_key: str | None = None) -> bool:
    try:
        VerifyKey(bytes.fromhex(public_key or public_key_hex())).verify(
            canonical_json(obj), bytes.fromhex(signature_hex)
        )
    except (BadSignatureError, ValueError):
        return False
    return True


def check_document(document: Any) -> str:
    """Check a signed document {payload, payload_hash, signature{public_key, signature}}.

    Returns "" when it is intact and signed by *this* deployment's key, otherwise
    the reason it is not. A document re-signed with someone else's key is
    self-consistent, so the key itself must match too.
    """
    if not isinstance(document, dict) or not isinstance(document.get("payload"), dict):
        return "not a signed document"
    payload = document["payload"]
    signature = document.get("signature") or {}
    if payload_hash(payload) != document.get("payload_hash"):
        return "the content does not match its hash: it was changed"
    if signature.get("public_key") != public_key_hex():
        return "signed with a different key, not by this deployment"
    if not verify(payload, str(signature.get("signature", ""))):
        return "the signature does not match the content"
    return ""
