"""Enforce the portable runtime boundary, independent of vendor/client availability."""

import ast
import sys
import zipfile
from pathlib import Path

from agent_brain.native_sync.install import export_cowork_plugin


def test_portable_runtime_has_closed_stdlib_only_dependencies(tmp_path):
    output = export_cowork_plugin(tmp_path / "plugin")
    prefix = "runtime/agent_brain/native_sync/"
    with zipfile.ZipFile(str(output) + ".zip") as bundle:
        modules = {
            Path(name).stem: bundle.read(name).decode()
            for name in bundle.namelist()
            if name.startswith(prefix) and name.endswith(".py")
        }
    assert {"hooks", "worker", "client", "storage", "contracts"} <= modules.keys()
    assert not {"api", "hub", "models", "install", "cli"} & modules.keys()
    for name, text in modules.items():
        for node in ast.walk(ast.parse(text)):
            if isinstance(node, ast.ImportFrom) and node.level:
                assert node.level == 1, (name, "must not import legacy parent package")
                assert node.module in modules, (name, "missing runtime dependency", node.module)
            elif isinstance(node, (ast.Import, ast.ImportFrom)):
                imports = (
                    [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module]
                )
                for dependency in imports:
                    assert dependency.split(".")[0] in sys.stdlib_module_names, (name, dependency)
