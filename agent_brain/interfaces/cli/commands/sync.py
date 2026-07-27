"""End-to-end encrypted multi-device sync commands."""

from __future__ import annotations

import json
import secrets
import socket

import typer

from agent_brain.interfaces.cli._app import sync_app
from agent_brain.interfaces.cli._shared import _brain_dir, _managed_components, _store_only
from agent_brain.product.encrypted_sync import (
    SyncConfig,
    apply_encrypted_objects,
    decode_recovery_key,
    encrypt_items,
    generate_recovery_key,
    load_sync_config,
    load_sync_key,
    pull_objects,
    push_objects,
    save_sync_setup,
    send_heartbeat,
)


@sync_app.command("init")
def sync_init(
    server: str = typer.Option(..., "--server", help="Cloud hub base URL"),
    api_key: str = typer.Option(..., "--api-key", envvar="AGENT_MEMORY_HUB_SYNC_API_KEY"),
    recovery_key: str | None = typer.Option(
        None,
        "--recovery-key",
        envvar="AGENT_MEMORY_HUB_SYNC_KEY",
        help="Existing device recovery key; omit to create a new brain key",
    ),
    device_name: str = typer.Option(socket.gethostname(), "--device-name"),
) -> None:
    """Initialize one device. The cloud never receives the recovery key."""

    key_text = recovery_key or generate_recovery_key()
    decode_recovery_key(key_text)
    config = SyncConfig(
        server=server.rstrip("/"),
        api_key=api_key,
        device_id=f"dev-{secrets.token_hex(12)}",
        device_name=device_name,
    )
    save_sync_setup(_brain_dir(), config, key_text)
    typer.echo(json.dumps(config.public_dict(), ensure_ascii=False))
    if recovery_key is None:
        typer.echo("RECOVERY_KEY=" + key_text)
        typer.echo("Store this once; another device needs it and the cloud cannot recover it.", err=True)


@sync_app.command("status")
def sync_status() -> None:
    """Show non-secret sync configuration."""

    config = load_sync_config(_brain_dir())
    load_sync_key(_brain_dir())
    typer.echo(json.dumps({**config.public_dict(), "ready": True}, ensure_ascii=False, indent=2))


@sync_app.command("push")
def sync_push() -> None:
    """Encrypt local Markdown items and upload opaque objects."""

    brain = _brain_dir()
    config = load_sync_config(brain)
    rows = encrypt_items(_store_only(), key=load_sync_key(brain), device_id=config.device_id)
    report = push_objects(config, rows)
    heartbeat = _heartbeat(config, len(rows))
    typer.echo(json.dumps({"encrypted": len(rows), "heartbeat": heartbeat, **report}, ensure_ascii=False))


@sync_app.command("pull")
def sync_pull() -> None:
    """Download, authenticate, decrypt, and conflict-safely merge remote items."""

    brain = _brain_dir()
    config = load_sync_config(brain)
    rows = pull_objects(config)
    with _managed_components() as (store, index, embedder):
        report = apply_encrypted_objects(
            store,
            rows,
            key=load_sync_key(brain),
            index=index,
            embedder=embedder,
        )
        object_count = sum(1 for _item, _body in store.iter_all())
    typer.echo(
        json.dumps(
            {**report.to_dict(), "heartbeat": _heartbeat(config, object_count)},
            ensure_ascii=False,
        )
    )


@sync_app.command("run")
def sync_run() -> None:
    """Push local objects, pull the shared encrypted set, then push resolved copies."""

    brain = _brain_dir()
    config = load_sync_config(brain)
    key = load_sync_key(brain)
    first = push_objects(
        config,
        encrypt_items(_store_only(), key=key, device_id=config.device_id),
    )
    remote = pull_objects(config)
    with _managed_components() as (store, index, embedder):
        applied = apply_encrypted_objects(
            store,
            remote,
            key=key,
            index=index,
            embedder=embedder,
        )
        final_rows = encrypt_items(store, key=key, device_id=config.device_id)
    final = push_objects(config, final_rows)
    heartbeat = _heartbeat(config, len(final_rows))
    typer.echo(
        json.dumps(
            {
                "push": first,
                "pull": applied.to_dict(),
                "converged_push": final,
                "heartbeat": heartbeat,
            },
            ensure_ascii=False,
        )
    )


@sync_app.command("heartbeat")
def sync_heartbeat() -> None:
    """Publish a privacy-minimal device liveness signal."""

    brain = _brain_dir()
    config = load_sync_config(brain)
    count = sum(1 for _item, _body in _store_only().iter_all())
    if not send_heartbeat(config, object_count=count):
        raise typer.Exit(code=1)
    typer.echo(json.dumps({"accepted": True, "objects": count}, ensure_ascii=False))


def _heartbeat(config: SyncConfig, object_count: int) -> bool:
    try:
        return send_heartbeat(config, object_count=object_count)
    except ValueError:
        # Syncing with a pre-heartbeat server remains functional.
        return False


__all__ = [
    "sync_heartbeat",
    "sync_init",
    "sync_pull",
    "sync_push",
    "sync_run",
    "sync_status",
]
