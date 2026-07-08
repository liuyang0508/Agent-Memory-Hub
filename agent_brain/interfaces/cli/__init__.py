"""Agent Memory Hub CLI package. ``app`` is the Typer entry point
(``memory = agent_brain.interfaces.cli:app``). Importing this package registers every command.
"""
from __future__ import annotations

from agent_brain.interfaces.cli._app import app  # noqa: F401
from agent_brain.interfaces.cli._shared import (  # noqa: F401
    CURRENT_SCHEMA_VERSION,
    get_default_embedder,
    _brain_dir,
    _store_only,
    _resolve_id,
    _open_components,
    _parse_enum,
    _evict_from_index,
    _doctor_offline,
)

# Importing each command module triggers its @app.command(...) self-registration
# and (via __all__) re-exports the command callables for tests that inspect them.
# Helpers above are re-exported so mock.patch("agent_brain.interfaces.cli.<helper>") resolves
# (the commands call the test-patched ones via late binding on this package).
from agent_brain.interfaces.cli.commands.crud import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.links import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.query import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.update import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.batch import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.doctor import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.gc import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.maintenance import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.evolution import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.lifecycle import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.io import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.api_docs import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.status import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.graph import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.insight import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.adapters import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.audit import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.tier import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.subapps import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.recall_drift import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.review import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.conversation import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.resource import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.product_capabilities import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.eval import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.loops import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.lint import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.wiki import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.codegraph import *  # noqa: F401,F403
from agent_brain.interfaces.cli.commands.hook import *  # noqa: F401,F403
