from pathlib import Path

import pytest
from django.test import override_settings

from core import signing


@pytest.fixture(autouse=True)
def isolated_key(tmp_path: Path):
    with override_settings(DATA_DIR=tmp_path):
        signing.signing_key.cache_clear()
        yield
    signing.signing_key.cache_clear()


def test_canonical_json_is_order_independent_and_compact() -> None:
    a = signing.canonical_json({"b": 1, "a": [1, 2], "ü": "é"})
    b = signing.canonical_json({"ü": "é", "a": [1, 2], "b": 1})
    assert a == b == '{"a":[1,2],"b":1,"ü":"é"}'.encode()


def test_sign_and_verify_roundtrip() -> None:
    record = {"event": "evt_01", "judge": "jdg_26", "reviews": 10}
    sig = signing.sign(record)
    assert sig.algorithm == "Ed25519"
    assert len(sig.value) == 128
    assert signing.verify(record, sig.value)
    assert signing.verify(record, sig.value, sig.public_key)


def test_any_change_to_the_payload_breaks_the_signature() -> None:
    record = {"event": "evt_01", "judge": "jdg_26", "reviews": 10}
    sig = signing.sign(record)
    assert not signing.verify({**record, "reviews": 11}, sig.value)
    assert not signing.verify(record, "00" * 64)
    assert not signing.verify(record, "not-hex")


def test_key_is_created_once_and_persisted(tmp_path: Path) -> None:
    first = signing.public_key_hex()
    signing.signing_key.cache_clear()
    assert signing.public_key_hex() == first
    assert (tmp_path / "keys" / "ed25519.seed").exists()
    assert signing.key_id() == signing.key_id(first)


def test_chain_hash_links_entries() -> None:
    h1 = signing.chain_hash(signing.GENESIS_HASH, signing.payload_hash({"n": 1}))
    h2 = signing.chain_hash(h1, signing.payload_hash({"n": 2}))
    tampered = signing.chain_hash(
        signing.chain_hash(signing.GENESIS_HASH, signing.payload_hash({"n": 999})),
        signing.payload_hash({"n": 2}),
    )
    assert h2 != tampered


def test_merkle_root_known_values() -> None:
    empty = signing.sha256_hex(b"")
    assert signing.merkle_root([]) == empty
    leaf = signing.sha256_hex(b"a")
    assert signing.merkle_root([leaf]) == leaf
    pair = signing.sha256_hex(f"{leaf}{leaf}".encode())
    assert signing.merkle_root([leaf, leaf]) == pair
    # Odd count: the last node is paired with itself.
    b = signing.sha256_hex(b"b")
    c = signing.sha256_hex(b"c")
    left = signing.sha256_hex(f"{leaf}{b}".encode())
    right = signing.sha256_hex(f"{c}{c}".encode())
    assert signing.merkle_root([leaf, b, c]) == signing.sha256_hex(f"{left}{right}".encode())


@pytest.mark.django_db
def test_public_key_endpoint_is_public_and_matches_signatures() -> None:
    from django.test import Client

    body = Client().get("/.well-known/raptor-desk-key").json()
    sig = signing.sign({"x": 1})
    assert body["alg"] == "Ed25519"
    assert body["public_key"] == sig.public_key
    assert body["key_id"] == sig.key_id


def _document(payload: dict) -> dict:
    sig = signing.sign(payload)
    return {
        "payload": payload,
        "payload_hash": signing.payload_hash(payload),
        "signature": {"public_key": sig.public_key, "signature": sig.value},
    }


def test_check_document_accepts_only_this_deployments_signature() -> None:
    from nacl.encoding import HexEncoder
    from nacl.signing import SigningKey

    good = _document({"number": "C-000001", "person": "Ada"})
    assert signing.check_document(good) == ""

    changed = {**good, "payload": {**good["payload"], "person": "Eve"}}
    assert "changed" in signing.check_document(changed)

    forged_payload = {"number": "C-000001", "person": "Eve"}
    other = SigningKey.generate()
    forged = {
        "payload": forged_payload,
        "payload_hash": signing.payload_hash(forged_payload),
        "signature": {
            "public_key": other.verify_key.encode(encoder=HexEncoder).decode(),
            "signature": other.sign(signing.canonical_json(forged_payload)).signature.hex(),
        },
    }
    assert "different key" in signing.check_document(forged)
    assert signing.check_document({"payload": "nope"}) == "not a signed document"
