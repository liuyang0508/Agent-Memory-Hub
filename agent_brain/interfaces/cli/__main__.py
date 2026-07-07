"""Enable ``python -m agent_brain.interfaces.cli`` execution.

The brain-pool shims invoke the CLI this way (``agent_runtime_kit/tools/_resolve-python.sh``
runs ``$PYTHON -m agent_brain.interfaces.cli "$@"``, which the write-memory hook and others
depend on). The pre-split cli.py module ended with ``if __name__ == "__main__":
app()``; after splitting cli.py into the ``cli/`` package that guard moved here so
``-m`` execution keeps working unchanged.
"""
from agent_brain.interfaces.cli import app

app()
