"""Hub/API fixes from the recall-parity review: bounded /recall, honest 422s,
preview masking, contradictions reset on new revisions, operator page errors."""

import json

import pytest
from fastapi.testclient import TestClient

from agent_brain.native_sync import recalls_page
from agent_brain.native_sync.api import (
    RECALL_BODY_BYTES,
    RECALL_ITEM_FIELDS,
    RECALL_TOTAL_BYTES,
    create_app,
    recall_items,
)
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.recall import coverage_key, pack
from agent_brain.native_sync.storage import database
from test_hub import asset


@pytest.fixture
def api(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    who = hub.provision("Author", "Source")
    client = TestClient(create_app(hub))
    return hub, who, client, {"Authorization": "Bearer " + who["token"]}


def signals(hub, asset_id):
    with database(hub.path) as db:
        row = db.execute("SELECT * FROM recall_signals WHERE asset_id=?", (asset_id,)).fetchone()
    return dict(row) if row else None


# --- B7: /recall projects hook fields only and bounds bodies -------------------


def test_recall_response_stays_under_the_client_limit_for_large_memories(api):
    hub, who, client, headers = api
    for index in range(8):
        hub.put(
            who["token"],
            asset(
                f"big-{index}",
                body="审批复核 " + (f"m{index:02d} " * 240_000),
                index_text="INDEX_SECRET" * 1000,
            ),
        )
    response = client.post("/native/v1/recall", json={"query": "审批复核"}, headers=headers)
    assert response.status_code == 200
    assert len(response.content) < 1_000_000
    data = response.json()
    assert len(data["items"]) == 8
    for item in data["items"]:
        assert item["body_truncated"] is True
        assert len(item["body"].encode()) <= RECALL_BODY_BYTES
        assert item["body"].startswith("审批复核 ")  # cut at a character boundary, start intact
        assert "index_text" not in item  # memory kinds never index-text; never leak it
    assert sum(len(i["body"].encode()) for i in data["items"]) <= RECALL_TOTAL_BYTES
    assert "INDEX_SECRET" not in response.text
    # The hook can still pack the truncated bodies into its 4KB prompt budget.
    body, keys = pack(data["items"], covered=set(), max_bytes=4096, query="审批复核")
    assert body and keys and keys[0].startswith("big-0:1:")


def test_recall_small_memory_is_returned_verbatim_with_hook_fields_only(api):
    hub, who, client, headers = api
    record = hub.put(
        who["token"],
        asset(
            "small",
            body="审批复核 需要两人签字\n第二行😀",
            source_meta={"mtime": 1700000000, "declared": {"type": "feedback"}},
            upstream=[],
        ),
    )
    response = client.post("/native/v1/recall", json={"query": "审批复核"}, headers=headers)
    assert response.status_code == 200
    item = response.json()["items"][0]
    assert item["body"] == record["body"] and "body_truncated" not in item
    assert set(item) <= set(RECALL_ITEM_FIELDS) | {"body", "semantic_match", "recall"}
    assert not {
        "score",
        "retrieval_ranks",
        "duplicate_sources",
        "source_meta",
        "index_text",
        "content_hash",
        "observed_at",
        "encoding",
        "hopfield_match",
    } & set(item)
    # Everything pack(), coverage_key(), the hook's provenance allowlist and
    # lineage.covered_records read is present.
    for key in (
        "asset_id",
        "revision",
        "body_hash",
        "kind",
        "path",
        "product",
        "project",
        "producer_name",
        "producer_member_id",
        "producer_unknown",
        "uploader_member_id",
        "uploader_name",
        "tenant_id",
        "source_device_id",
        "source_store",
        "title",
        "role",
        "deleted",
        "part_index",
        "part_count",
        "document_id",
        "document_hash",
        "upstream",
        "verified",
    ):
        assert key in item, key
    assert item["verified"] is True and item["role"] == "navigation"
    assert coverage_key(item) == coverage_key(record)
    assert set(item["recall"]) == {
        "trace_id",
        "routes",
        "final_rank",
        "nature",
        "decay_weight",
        "state",
    }
    server_pack = pack([record], covered=set(), query="审批复核")
    assert pack([item], covered=set(), query="审批复核") == server_pack


def test_recall_items_budget_covers_transcript_index_text_and_total_cap():
    base = {"asset_id": "t", "revision": 1, "body_hash": "h", "kind": "transcript"}
    long_text = "甲乙丙丁" * 40_000  # 480KB of UTF-8, 4 bytes per character boundary
    first = recall_items([{**base, "body": long_text, "index_text": "x" * 10, "secret": 1}])[0]
    assert "secret" not in first
    assert first["index_text"] == "x" * 10 and "index_text_truncated" not in first
    assert first["body_truncated"] and len(first["body"].encode()) <= RECALL_BODY_BYTES
    assert first["body"].encode() == long_text.encode()[: len(first["body"].encode())]
    # Nine 64KB bodies exceed the 512KB total: the ninth gets nothing, flagged.
    items = recall_items([{**base, "body": "a" * RECALL_BODY_BYTES} for _ in range(9)])
    assert [len(i["body"]) for i in items] == [RECALL_BODY_BYTES] * 8 + [0]
    assert items[8]["body_truncated"] is True and "body_truncated" not in items[0]
    memory = recall_items([{**base, "kind": "memory", "body": "ok", "index_text": "LEAK"}])[0]
    assert memory["body"] == "ok" and "index_text" not in memory


def test_recall_passes_an_unrecorded_trace_through_as_null(api, monkeypatch):
    hub, who, client, headers = api
    record = hub.put(who["token"], asset("a", body="审批复核"))

    def retrieve(token, query, **kwargs):
        hub.identity(token)
        return {
            "items": [{**record, "verified": True, "recall": {"trace_id": None, "state": "x"}}],
            "retrieval": {"mode": "lexical", "degraded": True, "reasons": [], "trace_id": None},
        }

    monkeypatch.setattr(hub, "retrieve", retrieve, raising=False)
    assert hub.recall(who["token"], "审批复核")["trace_id"] is None
    response = client.post("/native/v1/recall", json={"query": "审批复核"}, headers=headers)
    assert response.status_code == 200
    data = response.json()
    assert data["trace_id"] is None and "trace_id" not in data["retrieval"]
    assert data["items"][0]["recall"]["trace_id"] is None


# --- B25: validation errors never echo the request body ------------------------


def test_validation_errors_carry_only_location_message_and_type(api):
    hub, who, client, headers = api
    canary = "SECRET_BODY_不得回显"
    response = client.post(
        "/native/v1/assets",
        headers=headers,
        json=asset("n", body=canary, source_meta={"declared": {"body": "smuggled"}}),
    )
    assert response.status_code == 422
    assert canary not in response.text and "smuggled" not in response.text
    detail = response.json()["detail"]
    assert detail and all(set(entry) == {"loc", "msg", "type"} for entry in detail)
    assert detail[0]["loc"][-1] == "source_meta"


@pytest.mark.parametrize(
    "field",
    [
        '"source_meta": {"mtime": NaN}',
        '"source_meta": {"mtime": Infinity}',
        '"source_meta": {"mtime": -Infinity}',
        '"part_index": NaN',
        '"base_revision": Infinity',
        '"upstream": [NaN]',
    ],
)
def test_non_finite_numbers_in_the_body_are_a_422_not_a_409(api, field):
    # FastAPI's default handler echoes the offending input; a NaN in it fails
    # JSON rendering, which the ValueError handler then reported as a 409.
    hub, who, client, headers = api
    literal = field.split(":", 1)[1].strip(" {}[]").split(" ")[-1]
    payload = json.dumps(asset("n")).rstrip("}") + ", %s}" % field
    response = client.post(
        "/native/v1/assets",
        headers={**headers, "Content-Type": "application/json"},
        content=payload,
    )
    assert response.status_code == 422, response.text
    assert "Out of range" not in response.text and literal not in response.text
    assert all(set(entry) == {"loc", "msg", "type"} for entry in response.json()["detail"])


def test_malformed_json_and_wrong_query_types_are_422(api):
    hub, who, client, headers = api
    broken = client.post(
        "/native/v1/assets",
        headers={**headers, "Content-Type": "application/json"},
        content='{"asset_id": ',
    )
    assert broken.status_code == 422
    assert all(set(entry) == {"loc", "msg", "type"} for entry in broken.json()["detail"])
    assert client.get("/native/v1/recalls?trace_id=zz", headers=headers).status_code == 422


# --- B21: recall_query_preview is validated and masks history when off ----------


def test_query_preview_setting_is_normalised_and_other_values_are_refused(api):
    hub, who, client, headers = api
    operator = hub.admin_create("ops")
    hub.admin_setting(operator, "recall_query_preview", " OFF ")
    assert hub.setting("recall_query_preview") == "off"
    hub.admin_setting(operator, "recall_query_preview", "On")
    assert hub.setting("recall_query_preview") == "on"
    for bad in ("maybe", "0", "false", "off; drop"):
        with pytest.raises(ValueError, match="on or off"):
            hub.admin_setting(operator, "recall_query_preview", bad)
    assert hub.setting("recall_query_preview") == "on"
    response = client.post(
        "/native/v1/admin/settings",
        json={"key": "recall_query_preview", "value": "yes"},
        headers={"Authorization": "Bearer " + operator},
    )
    assert response.status_code == 409
    hub.admin_setting(operator, "recall_query_preview", "")
    assert hub.setting("recall_query_preview") == ""
    # Other settings keep their free-form values.
    hub.admin_setting(operator, "feishu_tenant", "Org-X")
    assert hub.setting("feishu_tenant") == "Org-X"


def test_switching_previews_off_hides_previously_recorded_previews(api):
    hub, who, client, headers = api
    operator = hub.admin_create("ops")
    hub.put(who["token"], asset("a", body="审批复核"))
    trace_id = hub.retrieve(who["token"], "审批复核 private prompt")["retrieval"]["trace_id"]
    assert hub.recalls(who["token"])["traces"][0]["query_preview"] == "审批复核 private prompt"
    hub.admin_setting(operator, "recall_query_preview", "OFF")
    for traces in (
        hub.recalls(who["token"])["traces"],
        hub.recalls(who["token"], trace_id=trace_id)["traces"],
        hub.admin_recalls(operator)["traces"],
        hub.admin_recalls(operator, trace_id=trace_id)["traces"],
        client.get("/native/v1/recalls", headers=headers).json()["traces"],
        client.get(
            "/native/v1/admin/recalls/data?trace_id=" + trace_id,
            headers={"Authorization": "Bearer " + operator},
        ).json()["traces"],
    ):
        assert traces and all(t["query_preview"] == "" for t in traces)
        assert "private prompt" not in json.dumps(traces, ensure_ascii=False)
    # New traces are recorded without a preview while off; shape is unchanged.
    later = hub.retrieve(who["token"], "审批复核 another prompt")["retrieval"]["trace_id"]
    with database(hub.path) as db:
        row = db.execute("SELECT * FROM recall_events WHERE trace_id=?", (later,)).fetchone()
    assert row["query_preview"] == "" and len(row["query_digest"]) == 64
    # Masking is a read-path policy: clearing the setting shows the history again.
    hub.admin_setting(operator, "recall_query_preview", "")
    listed = {t["trace_id"]: t["query_preview"] for t in hub.recalls(who["token"])["traces"]}
    assert listed == {trace_id: "审批复核 private prompt", later: ""}


# --- B28: a new revision drops contradictions, keeps delivery history ----------


def feedback(hub, token, trace_id, asset_id, *events):
    return hub.recall_feedback(
        token, trace_id, [{"asset_id": asset_id, "revision": 1, "event": e} for e in events]
    )


def test_new_revision_clears_contradictions_and_negative_gain_only(api):
    hub, who, client, headers = api
    hub.put(who["token"], asset("a", body="审批复核 第一版"))
    hub.put(who["token"], asset("b", body="审批复核 第一版 另一条"))
    trace_id = hub.retrieve(who["token"], "审批复核")["retrieval"]["trace_id"]
    feedback(hub, who["token"], trace_id, "a", "emitted", "confirmed", "cite", "contradict")
    # Three members endorse b, one contradicts it: its gain stays positive.
    feedback(hub, who["token"], trace_id, "b", "emitted", "cite", "contradict")
    for name in ("two", "three"):
        other = hub.provision(name, name + "-device")
        feedback(hub, other["token"], trace_id, "b", "support")
    before_a, before_b = signals(hub, "a"), signals(hub, "b")
    assert before_a["contradict"] == 1 and before_a["gain"] < 0
    assert before_b["contradict"] == 1 and before_b["gain"] > 0
    assert (before_a["delivered"], before_a["confirmed"], before_a["cited"]) == (1, 1, 1)
    # Idempotent replay of the same content is not a new revision: nothing moves.
    replay = hub.put(who["token"], asset("a", body="审批复核 第一版"))
    assert replay["revision"] == 1 and signals(hub, "a") == before_a
    replay = hub.put(
        who["token"],
        asset("a", body="审批复核 第一版", source_meta={"mtime": 1700000000}),
    )
    assert replay["revision"] == 1 and signals(hub, "a") == before_a
    # The corrected text starts without the contradictions filed against the old one.
    corrected = hub.put(who["token"], asset("a", body="审批复核 第二版", base_revision=1))
    assert corrected["revision"] == 2
    after_a = signals(hub, "a")
    assert after_a["contradict"] == 0 and after_a["gain"] == 0
    for field in ("delivered", "confirmed", "cited", "support", "last_delivered", "last_cited"):
        assert after_a[field] == before_a[field], field
    assert signals(hub, "b") == before_b  # Untouched asset keeps its contradiction.
    hub.put(who["token"], asset("b", body="审批复核 第二版 另一条", base_revision=1))
    after_b = signals(hub, "b")
    assert after_b["contradict"] == 0 and after_b["gain"] == pytest.approx(before_b["gain"])
    assert after_b["support"] == before_b["support"]
    # The new revision ranks without the contradiction multiplier.
    again = hub.retrieve(who["token"], "审批复核")
    candidate = next(c for c in again["trace"]["candidates"] if c["asset_id"] == "a")
    assert candidate["signals"]["contradict"] == 0 and candidate["revision"] == 2


# --- B34: operator page shows post-login errors where the operator looks --------


def test_recalls_page_reports_errors_in_the_operations_row_and_relogs_on_401(api):
    hub, who, client, headers = api
    page = client.get("/native/v1/admin/recalls").text
    assert page == recalls_page.html()
    ops = page[page.index('<div id="ops">') : page.index('<div id="content">')]
    assert 'id="opsError"' in ops  # the error slot lives inside the operations row
    script = page[page.index("<script>") :]
    # Every failure path goes through fail(); a rejected token re-shows the login card.
    # A pasted token that is rejected says so; without a token the page points
    # at the Feishu login instead of calling a missing token "invalid".
    assert (
        'r.status === 401) { fail(token ? "令牌无效或已撤销" : '
        '"未登录：请先在管理后台用飞书登录，或输入管理令牌", true)'
    ) in script
    assert 'credentials: "same-origin"' in script
    assert "r.status === 422) { fail(" in script
    assert 'fail("读取失败（HTTP " + r.status + "）", false)' in script
    assert 'catch (e) { fail("网络错误", false)' in script
    assert "/^[a-f0-9]{24}$/.test(trace)" in script
    assert 'document.getElementById("ops").style.display = "none";' in script
    assert 'document.getElementById("login").style.display = "";' in script
    # Messages are written with textContent, never innerHTML; the only
    # innerHTML sink is the rendered content, whose interpolations go via esc().
    assert 'document.getElementById("opsError").textContent = message;' in script
    assert 'document.getElementById("loginError").textContent = message;' in script
    assert script.count("innerHTML") == 1
    assert "opsError" not in script[script.index("function renderList") :]
    # Success clears both error slots so stale messages do not linger.
    success = script[script.index("let d;") : script.index("function renderList")]
    assert 'document.getElementById("opsError").textContent = "";' in success
    assert 'document.getElementById("loginError").textContent = "";' in success
