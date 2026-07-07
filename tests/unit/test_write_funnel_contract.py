"""Contract tests for the single external write funnel."""

from __future__ import annotations

from pathlib import Path

import pytest


ENTRYPOINT_FILES = [
    "agent_brain/interfaces/sdk/write.py",
    "agent_brain/agent_integrations/hermes/remember.py",
    "agent_brain/agent_integrations/hermes/import_export_tools.py",
    "web/api/routes/item_crud.py",
    "web/api/routes/item_batch.py",
    "web/api/routes/item_imports.py",
]


def test_external_write_entrypoints_do_not_call_items_store_write_directly() -> None:
    root = Path(__file__).resolve().parents[2]
    offenders: list[str] = []
    for rel in ENTRYPOINT_FILES:
        text = (root / rel).read_text(encoding="utf-8")
        if ".write(" in text and "WriteService" not in text and "_write_service" not in text:
            offenders.append(rel)
        if "store.write(" in text:
            offenders.append(f"{rel}:store.write")

    assert offenders == []


def test_sdk_write_uses_audit_gate(tmp_path: Path) -> None:
    from agent_brain.interfaces.sdk import MemoryClient

    client = MemoryClient(brain_dir=tmp_path)

    with pytest.raises(ValueError, match="write blocked"):
        client.write(
            type="fact",
            title="private key recipe",
            summary="unsafe",
            body="-----BEGIN " + "RSA PRIVATE KEY-----",
        )

    assert not list((tmp_path / "items").glob("*.md"))


def test_hermes_remember_uses_write_service_gate(tmp_brain_dir: Path) -> None:
    from agent_brain.agent_integrations.hermes.provider import hub_remember
    from tests.unit.test_hermes_provider import _patch_brain

    with _patch_brain(tmp_brain_dir):
        result = hub_remember(
            content="-----BEGIN " + "RSA PRIVATE KEY-----",
            title="private key recipe",
        )

    assert result["stored"] is False
    assert result["status"] == "blocked"
    assert result["findings"]
    assert not list((tmp_brain_dir / "items").glob("*.md"))
