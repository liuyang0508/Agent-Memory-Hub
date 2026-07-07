"""Root Typer app + sub-typers. Owns only the app objects + wiring; command
bodies live in cli/commands/*. Importing a command module self-registers it."""
from __future__ import annotations

import typer

app = typer.Typer(help="Agent Memory Hub CLI")

audit_app = typer.Typer(help="Audit commands")
app.add_typer(audit_app, name="audit")

govern_app = typer.Typer(help="Governance commands")
app.add_typer(govern_app, name="govern")

tier_app = typer.Typer(help="Storage tier (hot/warm/cold) commands")
app.add_typer(tier_app, name="tier")

entity_app = typer.Typer(help="Derived entity/persona views")
app.add_typer(entity_app, name="entity")

wiki_app = typer.Typer(help="LLM-Wiki workbench commands")
app.add_typer(wiki_app, name="wiki")

adapter_app = typer.Typer(help="Agent adapter management (install/uninstall brain-pool integration)")
app.add_typer(adapter_app, name="adapter")

recall_drift_app = typer.Typer(help="Recall drift reporting commands")
app.add_typer(recall_drift_app, name="recall-drift")

review_app = typer.Typer(help="Review needs-review memory candidates")
app.add_typer(review_app, name="review")

conversation_app = typer.Typer(help="Raw conversation evidence commands")
app.add_typer(conversation_app, name="conversation")

resource_app = typer.Typer(help="Resource and extraction evidence commands")
app.add_typer(resource_app, name="resource")

profile_app = typer.Typer(help="Agent memory profile export commands")
app.add_typer(profile_app, name="profile")

benchmark_app = typer.Typer(help="Benchmark and release-gate commands")
app.add_typer(benchmark_app, name="benchmark")

eval_app = typer.Typer(help="Memory eval scenario harness commands")
app.add_typer(eval_app, name="eval")

headroom_app = typer.Typer(help="Optional Headroom compression bridge")
app.add_typer(headroom_app, name="headroom")

loop_app = typer.Typer(help="Loop Engineering ledger commands")
app.add_typer(loop_app, name="loop")

codegraph_app = typer.Typer(help="Optional external code graph provider commands")
app.add_typer(codegraph_app, name="codegraph")
