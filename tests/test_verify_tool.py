import copy
import importlib.util
import json
from io import StringIO
from pathlib import Path

import pytest
from django.core.management import call_command
from nacl.encoding import HexEncoder
from nacl.signing import SigningKey

from apps.events import services as event_services
from apps.events.models import Event, Phase
from apps.judging import ledger, snapshots
from core import signing
from core.policy import Principal

ROOT = Path(__file__).resolve().parent.parent
_spec = importlib.util.spec_from_file_location("verify_tool", ROOT / "tools" / "verify.py")
tool = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(tool)

ORGANIZER = Principal(user_id="organizer", email="organizer@raptor-desk.local")


def test_rfc_8032_test_vector_1() -> None:
    public = bytes.fromhex("d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a")
    signature = bytes.fromhex(
        "e5564300c360ac729086e2cc806e828a84877f1eb8e5d974d873e065224901555fb8821590a33bacc61e39701cf9b46bd25bf5f0595bbe24655141438e7a100b"
    )
    assert tool.ed25519_verify(public, b"", signature)
    assert not tool.ed25519_verify(public, b"x", signature)


def test_canonical_json_matches_the_portal() -> None:
    sample = {"b": [1, 2.5, None], "a": "é", "c": {"z": 1, "y": True}}
    assert tool.canonical_json(sample) == signing.canonical_json(sample)


@pytest.fixture
def frozen(db, settings, tmp_path) -> Event:
    settings.DATA_DIR = tmp_path
    signing.signing_key.cache_clear()
    fixtures = Path(settings.REPO_DIR) / "data" / "fixtures.json"
    call_command("seed", fixtures=str(fixtures), stdout=StringIO())
    event = Event.objects.get(pk="evt_01")
    event_services.transition(ORGANIZER, event, Phase.DELIBERATION, reason="Judging closed")
    yield event
    signing.signing_key.cache_clear()


def test_portal_documents_verify_offline_and_forgeries_do_not(frozen: Event) -> None:
    key = signing.public_key_hex()
    document = snapshots.signed_document(snapshots.freeze(ORGANIZER, frozen))
    assert tool.check(document, key)[0]

    changed = copy.deepcopy(document)
    changed["payload"]["ranking"][0]["score"] = 5.0
    ok, reason = tool.check(changed, key)
    assert not ok and "changed" in reason

    other = SigningKey.generate()
    resigned = copy.deepcopy(changed)
    resigned["payload_hash"] = tool.sha256_hex(tool.canonical_json(resigned["payload"]))
    resigned["signature"] = {
        "public_key": other.verify_key.encode(encoder=HexEncoder).decode(),
        "signature": other.sign(tool.canonical_json(resigned["payload"])).signature.hex(),
    }
    ok, reason = tool.check(resigned, key)
    assert not ok and "different key" in reason


def test_ledger_exports_verify_offline_and_tampering_is_located(frozen: Event) -> None:
    export = ledger.export(frozen)
    ok, message = tool.check(export, signing.public_key_hex())
    assert ok and "126 entries" in message
    export["entries"][40]["payload"]["scores"]["quality"] = 1
    ok, message = tool.check(export, None)
    assert not ok and message.startswith("entry 41")


def test_command_line_exit_codes(frozen: Event, tmp_path: Path, capsys) -> None:
    document = snapshots.signed_document(snapshots.freeze(ORGANIZER, frozen))
    good = tmp_path / "good.json"
    good.write_text(json.dumps(document), encoding="utf-8")
    assert tool.main([str(good), "--public-key", signing.public_key_hex()]) == 0
    assert "VALID" in capsys.readouterr().out
    document["payload"]["method"] = "raw"
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps(document), encoding="utf-8")
    assert tool.main([str(bad)]) == 1
    assert tool.main([str(tmp_path / "missing.json")]) == 2
