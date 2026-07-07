"""CLI batch item mutation commands."""

from __future__ import annotations

from agent_brain.interfaces.cli._app import app
from agent_brain.interfaces.cli._shared import *  # noqa: F401,F403


@app.command(name="batch-confirm")
def batch_confirm(
    item_ids: list[str] = typer.Argument(..., help="Item IDs to confirm"),
    confidence: float = typer.Option(0.9, "--confidence", help="Confidence to set"),
) -> None:
    """Confirm multiple memory items at once — sets confidence for each."""
    store = _store_only()
    brain = _brain_dir()
    idx = HubIndex(db_path=brain / "index.db")
    # Clamp to [0,1] so out-of-range values match HubIndex.update_confidence's
    # clamp instead of being rejected per-item by the md schema (confidence
    # ge=0/le=1) — keeping the md file and index consistent.
    confidence = min(max(confidence, 0.0), 1.0)
    ok, fail = 0, 0
    for item_id in item_ids:
        try:
            store.update_frontmatter(item_id, confidence=confidence)
            idx.update_confidence(item_id, confidence)
            ok += 1
            typer.echo(f"  ✓ {item_id}")
        except FileNotFoundError:
            fail += 1
            typer.echo(f"  ✗ {item_id} (not found)", err=True)
        except Exception as e:
            fail += 1
            typer.echo(f"  ✗ {item_id} ({e})", err=True)
    idx.close()
    typer.echo(f"\nConfirmed {ok}/{len(item_ids)} items (confidence={confidence})")


@app.command(name="batch-archive")
def batch_archive(
    item_ids: list[str] = typer.Argument(..., help="Item IDs to archive"),
) -> None:
    """Archive multiple memory items — moves to items/archived/."""
    import shutil

    store = _store_only()
    brain = _brain_dir()
    idx = HubIndex(db_path=brain / "index.db")
    archive_dir = store.items_dir / "archived"
    archive_dir.mkdir(exist_ok=True)
    ok, fail = 0, 0
    for item_id in item_ids:
        src = store.items_dir / f"{item_id}.md"
        if not src.exists():
            fail += 1
            typer.echo(f"  ✗ {item_id} (not found)", err=True)
            continue
        try:
            dst = archive_dir / f"{item_id}.md"
            shutil.move(str(src), str(dst))
            try:
                idx.delete(item_id)
            except Exception:
                pass
            ok += 1
            typer.echo(f"  ✓ {item_id} → archived/")
        except Exception as e:
            fail += 1
            typer.echo(f"  ✗ {item_id} ({e})", err=True)
    idx.close()
    typer.echo(f"\nArchived {ok}/{len(item_ids)} items")


__all__ = ["batch_confirm", "batch_archive"]
