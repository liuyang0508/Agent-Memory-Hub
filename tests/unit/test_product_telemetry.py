from __future__ import annotations

import json
import stat
from pathlib import Path


class _Response:
    status = 202

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False


def test_product_telemetry_is_opt_in_and_payload_is_bounded(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.platform import product_telemetry

    brain = tmp_path / "brain"
    sent = []
    monkeypatch.setenv("BRAIN_DIR", str(brain))
    monkeypatch.delenv("AMH_TELEMETRY", raising=False)
    monkeypatch.setattr(
        product_telemetry.urllib.request,
        "urlopen",
        lambda request, timeout: sent.append((request, timeout)) or _Response(),
    )

    assert product_telemetry.emit("install", channel="shell") is False
    assert sent == []

    config = product_telemetry.enable()
    assert product_telemetry.emit("install", channel="shell", adapter="codex") is True
    assert len(sent) == 1
    request, timeout = sent[0]
    payload = json.loads(request.data)

    assert timeout == 2.0
    assert payload == {
        "adapter": "codex",
        "anonymous_id": config["anonymous_id"],
        "architecture": payload["architecture"],
        "channel": "shell",
        "event": "install",
        "platform": payload["platform"],
        "product_version": payload["product_version"],
        "schema_version": 1,
    }
    serialized = json.dumps(payload)
    assert str(Path.home()) not in serialized
    assert "prompt" not in serialized
    assert "memory" not in serialized
    assert "session" not in serialized

    config_path = brain / "product-telemetry.json"
    assert stat.S_IMODE(config_path.stat().st_mode) == 0o600
    audit_files = list((brain / "audit-log").glob("outbound-*.json"))
    assert len(audit_files) == 1
    assert json.loads(audit_files[0].read_text())["approved_by"] == "local opt-in"


def test_active_telemetry_is_throttled_and_disable_is_immediate(
    tmp_path: Path,
    monkeypatch,
) -> None:
    from agent_brain.platform import product_telemetry

    sent = []
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    monkeypatch.setattr(
        product_telemetry.urllib.request,
        "urlopen",
        lambda request, timeout: sent.append(request) or _Response(),
    )
    product_telemetry.enable()

    assert product_telemetry.emit("active", adapter="claude-code") is True
    assert product_telemetry.emit("active", adapter="claude-code") is False
    assert len(sent) == 1

    product_telemetry.disable()
    assert product_telemetry.emit("install") is False
    assert len(sent) == 1


def test_explicit_environment_preference_is_persisted(tmp_path: Path, monkeypatch) -> None:
    from agent_brain.platform import product_telemetry

    monkeypatch.setenv("BRAIN_DIR", str(tmp_path / "brain"))
    monkeypatch.setenv("AMH_TELEMETRY", "1")
    assert product_telemetry.configure_from_environment()["enabled"] is True
    monkeypatch.setenv("AMH_TELEMETRY", "0")
    assert product_telemetry.configure_from_environment()["enabled"] is False
