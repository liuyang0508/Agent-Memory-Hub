"""1.4.29: a whole conversation cannot be found or read through the organization tools."""

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from agent_brain.native_sync import cli, history
from agent_brain.native_sync.api import create_app
from agent_brain.native_sync.hub import Hub
from agent_brain.native_sync.org_tools import KINDS, OrganizationTools
from test_org_mcp import hub as tool_hub  # noqa: F401  (fixture re-export)


def _put(hub, token, key, kind, body):
    return hub.put(
        token,
        {
            "asset_id": key,
            "source_store": "native",
            "product": "claude-code",
            "kind": kind,
            "path": key + (".jsonl" if kind == "transcript" else ".md"),
            "body": body,
            "index_text": body if kind == "transcript" else None,
        },
    )


@pytest.fixture
def env(tmp_path):
    hub = Hub(tmp_path / "hub.sqlite")
    owner = hub.provision("Alice", "A", tenant="org")
    peer = hub.provision("Bob", "B", tenant="org")
    _put(hub, owner["token"], "note", "memory", "upgrade with upgrade.sh on the box")
    _put(hub, owner["token"], "talk", "transcript", "upgrade with upgrade.sh on the box")
    client = TestClient(create_app(hub))
    return client, {"Authorization": "Bearer " + peer["token"]}


def test_the_tool_refuses_to_ask_for_transcripts(tool_hub):  # noqa: F811
    assert "transcript" not in KINDS
    for kinds in (["transcript"], ["memory", "transcript"]):
        found = OrganizationTools(tool_hub.state).search("anything", kinds=kinds)
        assert found["ok"] is False and found["code"] == "invalid_arguments"
    assert not [call for call in tool_hub.calls if call[0].endswith("/retrieve")]


def test_the_hub_refuses_retrieve_for_older_clients(env):
    client, headers = env
    response = client.post(
        "/native/v1/retrieve", headers=headers, json={"query": "upgrade", "kinds": ["transcript"]}
    )
    assert response.status_code == 422
    mixed = client.post(
        "/native/v1/retrieve",
        headers=headers,
        json={"query": "upgrade", "kinds": ["memory", "transcript"]},
    )
    assert mixed.status_code == 422


def test_memory_is_still_found(env):
    client, headers = env
    response = client.post("/native/v1/retrieve", headers=headers, json={"query": "upgrade"})
    assert response.status_code == 200
    assert [item["asset_id"] for item in response.json()["items"]] == ["note"]


def test_the_old_search_leaves_them_out(env):
    client, headers = env
    items = client.post("/native/v1/search", headers=headers, json={"query": "upgrade"}).json()
    assert [item["kind"] for item in items["items"]] == ["memory"]


@pytest.mark.parametrize("path", ["read", "related"])
def test_read_and_related_answer_as_if_absent(env, path):
    client, headers = env
    talk = client.post(
        f"/native/v1/{path}", headers=headers, json={"asset_id": "talk", "revision": 1}
    )
    absent = client.post(
        f"/native/v1/{path}", headers=headers, json={"asset_id": "nothing", "revision": 1}
    )
    assert talk.status_code == absent.status_code
    assert talk.json() == absent.json()
    assert "asset_unavailable" in talk.text
    note = client.post(
        f"/native/v1/{path}", headers=headers, json={"asset_id": "note", "revision": 1}
    )
    assert note.status_code == 200


def test_quick_selection_on_the_page_stops_at_enabled_types():
    from agent_brain.native_sync import picker

    assert 'r.scope_enabled !== false && r.risk !== "blocked"' in picker.PAGE


def _row(name, kind):
    return {
        "name": name,
        "title": name,
        "path": "/tmp/" + name,
        "kind": kind,
        "product": "claude-code",
        "product_name": "Claude Code",
        "bytes": 1024,
        "mtime": 0,
        "risk": "clean",
        "reasons": [],
    }


@pytest.mark.parametrize(
    "answer, extra, count",
    [("clean", [], 1), ("all", [], 1), ("clean", ["--kind", "transcript"], 1), ("1,2", [], 2)],
)
def test_quick_selection_in_the_terminal_stops_at_enabled_types(
    tmp_path, monkeypatch, answer, extra, count
):
    rows = [_row("note.md", "memory"), _row("talk.jsonl", "transcript")]
    monkeypatch.setattr(history, "items", lambda *args, **kwargs: rows)
    result = CliRunner().invoke(
        cli.app,
        ["import", "--state", str(tmp_path / "none"), "--no-web", *extra],
        input=answer + "\nn\n",
    )
    assert f"将上传 {count} 个文件" in result.output, result.output
