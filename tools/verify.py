"""Check Raptor Desk signed documents and score ledgers, with nothing installed.

    python3 tools/verify.py results.json --portal http://localhost:8080
    python3 tools/verify.py certificate.json --public-key <hex>
    python3 tools/verify.py ledger.json

Accepts:
- a signed document: {"payload": ..., "payload_hash": ..., "signature": {...}}
  (results snapshots, judge protocols, certificates, participation records);
- a score-ledger export (GET /api/events/{id}/ledger).

--portal fetches the portal's public key from /.well-known/raptor-desk-key, and
--public-key takes it directly. Either way the document must be signed with
that key: a document re-signed with some other key is rejected. Without
either, the key carried in the document is used and the output says so.

Exit status 0 means valid, 1 means not valid, 2 means the input could not be read.
Python standard library only; Ed25519 verification follows RFC 8032, section 6.
"""

import argparse
import hashlib
import json
import sys
import urllib.request
from typing import Any

# --- Ed25519 verification (RFC 8032, section 6) ------------------------------------------

_P = 2**255 - 19
_Q = 2**252 + 27742317777372353535851937790883648493


def _inv(x: int) -> int:
    return pow(x, _P - 2, _P)


_D = -121665 * _inv(121666) % _P
_SQRT_M1 = pow(2, (_P - 1) // 4, _P)

Point = tuple[int, int, int, int]


def _add(a: Point, b: Point) -> Point:
    pa = (a[1] - a[0]) * (b[1] - b[0]) % _P
    pb = (a[1] + a[0]) * (b[1] + b[0]) % _P
    pc = 2 * a[3] * b[3] * _D % _P
    pd = 2 * a[2] * b[2] % _P
    e, f, g, h = pb - pa, pd - pc, pd + pc, pb + pa
    return (e * f, g * h, f * g, e * h)


def _mul(s: int, point: Point) -> Point:
    result: Point = (0, 1, 1, 0)
    while s > 0:
        if s & 1:
            result = _add(result, point)
        point = _add(point, point)
        s >>= 1
    return result


def _equal(a: Point, b: Point) -> bool:
    return (a[0] * b[2] - b[0] * a[2]) % _P == 0 and (a[1] * b[2] - b[1] * a[2]) % _P == 0


def _recover_x(y: int, sign: int) -> int | None:
    if y >= _P:
        return None
    x2 = (y * y - 1) * _inv(_D * y * y + 1)
    if x2 == 0:
        return None if sign else 0
    x = pow(x2, (_P + 3) // 8, _P)
    if (x * x - x2) % _P != 0:
        x = x * _SQRT_M1 % _P
    if (x * x - x2) % _P != 0:
        return None
    if (x & 1) != sign:
        x = _P - x
    return x


_GY = 4 * _inv(5) % _P
_GX = _recover_x(_GY, 0) or 0
_G: Point = (_GX, _GY, 1, _GX * _GY % _P)


def _decompress(data: bytes) -> Point | None:
    if len(data) != 32:
        return None
    y = int.from_bytes(data, "little")
    sign = y >> 255
    y &= (1 << 255) - 1
    x = _recover_x(y, sign)
    return None if x is None else (x, y, 1, x * y % _P)


def ed25519_verify(public_key: bytes, message: bytes, signature: bytes) -> bool:
    if len(public_key) != 32 or len(signature) != 64:
        return False
    a = _decompress(public_key)
    r = _decompress(signature[:32])
    if a is None or r is None:
        return False
    s = int.from_bytes(signature[32:], "little")
    if s >= _Q:
        return False
    h = int.from_bytes(hashlib.sha512(signature[:32] + public_key + message).digest(), "little")
    return _equal(_mul(s, _G), _add(r, _mul(h % _Q, a)))


# --- Raptor Desk formats ---------------------------------------------------------------------

GENESIS_HASH = "0" * 64


def canonical_json(obj: Any) -> bytes:
    """Sorted keys, no whitespace, UTF-8: the bytes Raptor Desk hashes and signs."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()


def sha256_hex(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _signed_by(obj: Any, signature_hex: str, public_key_hex: str) -> bool:
    try:
        return ed25519_verify(
            bytes.fromhex(public_key_hex), canonical_json(obj), bytes.fromhex(signature_hex)
        )
    except ValueError:
        return False


def check_document(document: dict[str, Any], expected_key: str | None) -> tuple[bool, str]:
    payload = document.get("payload")
    signature = document.get("signature") or {}
    if not isinstance(payload, dict):
        return False, "not a signed document"
    if sha256_hex(canonical_json(payload)) != document.get("payload_hash"):
        return False, "the content does not match its hash: it was changed"
    key = signature.get("public_key", "")
    if expected_key and key != expected_key:
        return False, "signed with a different key than the portal's"
    if not _signed_by(payload, signature.get("signature", ""), key):
        return False, "the signature does not match the content"
    kind = payload.get("kind", "document")
    label = payload.get("number", "")
    return True, f"valid {kind} {label}".strip()


def check_ledger(export: dict[str, Any], expected_key: str | None) -> tuple[bool, str]:
    key = export.get("public_key", "")
    if expected_key and key != expected_key:
        return False, "the ledger export names a different key than the portal's"
    prev = GENESIS_HASH
    entries = export.get("entries", [])
    for expected_seq, entry in enumerate(entries, start=1):
        seq = entry.get("seq")
        if seq != expected_seq:
            return False, f"entry {expected_seq} is missing"
        payload_hash = sha256_hex(canonical_json(entry.get("payload")))
        if payload_hash != entry.get("payload_hash"):
            return False, f"entry {seq}: the content was changed"
        chained = sha256_hex(f"{prev}:{payload_hash}".encode())
        if entry.get("prev_hash") != prev or entry.get("entry_hash") != chained:
            return False, f"entry {seq}: the chain is broken"
        if not _signed_by({"entry_hash": chained}, entry.get("signature", ""), key):
            return False, f"entry {seq}: the signature does not match"
        prev = chained
    return True, f"valid score ledger: {len(entries)} entries, head {prev[:16]}"


def check(data: dict[str, Any], expected_key: str | None) -> tuple[bool, str]:
    if data.get("kind") == "raptor-desk/score-ledger":
        return check_ledger(data, expected_key)
    return check_document(data, expected_key)


def portal_key(base_url: str) -> str:
    url = base_url.rstrip("/") + "/.well-known/raptor-desk-key"
    with urllib.request.urlopen(url, timeout=10) as response:  # noqa: S310 (user-given URL)
        return json.load(response)["public_key"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("file", help="a signed JSON document or a ledger export ('-' for stdin)")
    key = parser.add_mutually_exclusive_group()
    key.add_argument("--portal", help="portal URL to fetch the public key from")
    key.add_argument("--public-key", help="the portal's Ed25519 public key, hex")
    args = parser.parse_args(argv)
    try:
        if args.file == "-":
            raw = sys.stdin.read()
        else:
            with open(args.file, encoding="utf-8") as handle:
                raw = handle.read()
        data = json.loads(raw)
        expected = args.public_key or (portal_key(args.portal) if args.portal else None)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot read input: {exc}")
        return 2
    ok, message = check(data, expected)
    print(("VALID  " if ok else "INVALID  ") + message)
    if ok and expected is None:
        print("note: checked against the key inside the file; pass --portal to pin the key")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
