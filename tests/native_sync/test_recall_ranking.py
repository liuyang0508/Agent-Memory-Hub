"""Focused regressions for natural-question recall and bounded Cowork onboarding."""

import json
import time
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.client import NativeClient
from agent_brain.native_sync.common import PAIRING_MAX_TTL, digest
from agent_brain.native_sync.cowork_runtime import export_connection
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.install import export_cowork_plugin
from agent_brain.native_sync.recall import coverage_key, pack
from agent_brain.native_sync.storage import database
from test_hub import asset
from test_runtime import live as live


def test_natural_question_promotes_curated_memory_above_verbose_transcripts(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("Author", "device")
    for i in range(8):
        hub.put(
            who["token"],
            asset(
                f"log{i}",
                kind="transcript",
                title="灵鹤计划的验收标准是什么",
                body=("灵鹤计划的验收标准是什么？会话历史讨论，请核对验收标准。" * 160),
            ),
        )
    hub.put(
        who["token"],
        asset(
            "answer",
            title="灵鹤计划",
            body="灵鹤计划验收标准：37条检查逐项通过，异常单独签字确认。",
        ),
    )
    hits = hub.search(who["token"], "灵鹤计划的验收标准是什么", limit=50)
    assert next(i for i, r in enumerate(hits, 1) if r["asset_id"] == "answer") <= 2


def test_single_part_fast_path_keeps_integrity_without_whole_version_queries(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("Author", "device")
    hub.put(who["token"], asset("good", body="SINGLE_CANARY"))
    hub.put(
        who["token"],
        asset(
            "bad", body="SINGLE_CANARY", document_id="bad", document_hash=digest("not these bytes")
        ),
    )
    with patch.object(
        hub, "_version_proof", side_effect=AssertionError("single-part queried again")
    ):
        assert [r["asset_id"] for r in hub.search(who["token"], "SINGLE_CANARY")] == ["good"]
    with database(hub.path) as db:
        plan = db.execute(
            "EXPLAIN QUERY PLAN SELECT payload FROM assets WHERE tenant=? AND owner_device=? AND json_extract(payload,'$.source_store')=? AND json_extract(payload,'$.document_id')=? AND json_extract(payload,'$.document_hash')=?",
            ("linkcrux", who["device_id"], "store", "doc", "hash"),
        ).fetchall()
        assert any("assets_document_version" in r["detail"] for r in plan)


def test_long_first_item_does_not_starve_complete_memory_or_repeat_same_excerpt(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("敖丙", "device")
    log = hub.put(who["token"], asset("log", kind="transcript", body="word " * 1800))
    answer_body = "灵鹤计划验收标准：37条逐项确认。" + "完整正文内容。" * 35
    answer = hub.put(who["token"], asset("answer", body=answer_body))
    other = hub.put(who["token"], asset("other", body="其他简短的验收说明"))
    text, keys = pack([log, answer, other], covered=set(), query="验收标准")
    assert answer_body in text and other["body"] in text
    assert len(text.encode()) <= 4096 and len(keys) == 3
    assert coverage_key(answer) in keys
    assert coverage_key(log) not in keys
    assert pack([log, answer, other], covered=set(keys), query="验收标准")[0] == ""
    assert who["member_id"] not in text  # Routine display needs name/product, not internal keys.


def test_compact_provenance_disambiguates_same_name_members(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    a = hub.provision("Same", "a")
    b = hub.provision("Other", "b")
    with database(hub.path) as db:
        db.execute("UPDATE members SET name='Same' WHERE id=?", (b["member_id"],))
    first = hub.put(a["token"], asset("a", body="identical content"))
    second = hub.put(b["token"], asset("b", body="identical content"))
    text, keys = pack([first, second], covered=set())
    assert a["member_id"] in text and b["member_id"] in text
    assert len(keys) == 2 and text.count("identical content") == 1


def test_extended_pairing_stays_one_use_and_sponsor_revocable(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("Member", "sponsor")
    now = time.time()
    with patch("agent_brain.native_sync.hub.time.time", return_value=now):
        normal = hub.runtime_invite(who["token"])
        ticket = hub.runtime_invite(who["token"], ttl_seconds=PAIRING_MAX_TTL)
    assert normal["expires_at"] == now + 900
    assert ticket["expires_at"] == now + 7 * 86400
    with patch("agent_brain.native_sync.hub.time.time", return_value=now + 86400):
        paired = hub.enroll(ticket["invite"], "cowork")
        assert paired["member_id"] == who["member_id"]
        with pytest.raises(ValueError):
            hub.enroll(ticket["invite"], "again")
        revoked = hub.runtime_invite(who["token"], ttl_seconds=PAIRING_MAX_TTL)
        hub.revoke(who["device_id"])
        with pytest.raises(ValueError):
            hub.enroll(revoked["invite"], "later")


def test_pairing_lifetime_is_bounded_at_api_and_authority(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("Member", "sponsor")
    api = TestClient(create_app(hub))
    headers = {"Authorization": "Bearer " + who["token"]}
    for ttl in [0, True, PAIRING_MAX_TTL + 1]:
        assert (
            api.post(
                "/native/v1/runtime-invites", json={"ttl_seconds": ttl}, headers=headers
            ).status_code
            == 422
        )
        with pytest.raises(ValueError):
            hub.runtime_invite(who["token"], ttl_seconds=ttl)
    assert (
        api.post("/native/v1/runtime-invites", json={"ttl_seconds": PAIRING_MAX_TTL}).status_code
        == 401
    )


def test_cowork_long_pairing_file_is_private_and_absent_from_plugin(live, tmp_path):
    hub, url = live
    who = hub.provision("Member", "host")
    client = NativeClient.adopt(tmp_path / "state", url, who, "host")
    connection = tmp_path / "private" / "connection.json"
    policy = export_connection(client.root, connection)
    ticket = json.loads(connection.read_text())
    assert ticket["expires_at"] - time.time() > 6 * 86400
    assert connection.stat().st_mode & 0o077 == 0
    plugin = export_cowork_plugin(tmp_path / "plugin", policy=policy)
    with zipfile.ZipFile(str(plugin) + ".zip") as z:
        raw = b"\n".join(z.read(name) for name in z.namelist())
    assert ticket["invite"].encode() not in raw and who["token"].encode() not in raw
