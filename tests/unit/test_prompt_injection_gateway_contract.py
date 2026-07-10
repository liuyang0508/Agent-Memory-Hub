import ast
import re
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
PROMPT_SURFACES = (
    "agent_brain/interfaces/mcp/tools/search_tools.py",
    "agent_brain/interfaces/sdk/query.py",
    "agent_brain/interfaces/cli/commands/query.py",
    "agent_brain/memory/recall/brief.py",
    "web/api/routes/item_search.py",
)
FORBIDDEN_FROM_IMPORTS = {
    "agent_brain.memory.context.context_packing": {
        "build_context_pack",
        "pack_decisions",
    },
    "agent_brain.memory.context.context_firewall": {"ContextFirewall"},
}
FORBIDDEN_SYMBOLS = frozenset(
    symbol
    for symbols in FORBIDDEN_FROM_IMPORTS.values()
    for symbol in symbols
)


def _policy_bypass_violations(relative: str) -> list[str]:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
    module_aliases: dict[str, str] = {}
    violations: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            module = node.module or ""
            violations.extend(
                f"{module}:{alias.name}"
                for alias in node.names
                if alias.name in FORBIDDEN_SYMBOLS
                or (
                    alias.name == "*"
                    and module.startswith("agent_brain.memory.context")
                )
            )
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name in FORBIDDEN_FROM_IMPORTS:
                    local_name = alias.asname or alias.name.rsplit(".", 1)[-1]
                    module_aliases[local_name] = alias.name
                    violations.append(f"{alias.name}:module")

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_SYMBOLS:
                violations.append(f"call:{node.func.id}")
            elif (
                isinstance(node.func, ast.Attribute)
                and node.func.attr in FORBIDDEN_SYMBOLS
            ):
                violations.append(f"call:{node.func.attr}")
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name):
            module = module_aliases.get(node.value.id)
            if module and node.attr in FORBIDDEN_FROM_IMPORTS[module]:
                violations.append(f"{module}:{node.attr}")
    return sorted(set(violations))


def _imports_injection_gateway(relative: str) -> bool:
    tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"), filename=relative)
    return any(
        (
            isinstance(node, ast.ImportFrom)
            and node.module == "agent_brain.memory.context.injection_gateway"
        )
        or (
            isinstance(node, ast.Import)
            and any(
                alias.name == "agent_brain.memory.context.injection_gateway"
                for alias in node.names
            )
        )
        for node in ast.walk(tree)
    )


def test_prompt_surfaces_do_not_import_or_use_policy_pack_bypasses():
    for relative in PROMPT_SURFACES:
        assert _policy_bypass_violations(relative) == [], relative


def test_prompt_surfaces_reference_injection_gateway():
    for relative in PROMPT_SURFACES:
        assert _imports_injection_gateway(relative), relative


def test_hook_selects_the_gateway_backed_cli_mode():
    source = (ROOT / "agent_runtime_kit/hooks/inject-context.sh").read_text(
        encoding="utf-8",
    )
    match = re.search(r"SEARCH_ARGS=\((.*?)\n\)", source, flags=re.DOTALL)
    assert match is not None
    assert re.search(r'^[ \t]*"--context-firewall"[ \t]*$', match.group(1), re.MULTILINE)
