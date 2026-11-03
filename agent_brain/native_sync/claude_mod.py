"""A Claude Code mod that draws prompt-hook recall as a collapsible transcript row.

Claude Code prints a hook's ``systemMessage`` as plain text, so a table cannot
be colored or folded there. The mod marks each session it draws for under
``<state>/rich-display/``; the prompt hook then writes its view to
``<state>/recall-view/<session>.json`` instead, and the mod places a row with a
colored title, a one-line summary and the call, parameters and table behind
an expand button. A stale mark (the mod unloaded) brings the plain message back.
"""

from __future__ import annotations

import json
from pathlib import Path

from .common import atomic_text

NAME = "amh-recall-view"
REGISTER = "import { atom, read, update } from 'claude-code'\nimport type { Register } from 'claude-code'\n\nimport type { RecallPanel } from '../types'\n\n// The AMH client state directory: the `--state` of the prompt hook.\nconst STATE = __AMH_STATE__\nconst SAFE = /[^A-Za-z0-9_-]/g\n\nconst panels = atom({ plugin: 'amh-recall-view', key: 'panels' } as const, {}, { shape: 'panels-1' })\nconst open = atom({ plugin: 'amh-recall-view', key: 'open' } as const, [], { shape: 'open-1' })\n\nexport const register: Register = on => {\n  // Tell the hook this session is drawn here, so it hands the view over\n  // instead of printing it as plain text. Refreshed before every prompt: the\n  // hook trusts a fresh mark only, so a disabled mod falls back by itself.\n  on('classic.UserPromptSubmit', async ($, e, next) => {\n    const name = e.session_id.replace(SAFE, '').slice(0, 128)\n    if (name) {\n      await $.fs.write(`${STATE}/rich-display/${name}`, String(Date.now())).catch(() => undefined)\n    }\n    const startedAt = Date.now()\n    const result = await next(e)\n    if (!name) return result\n    try {\n      const raw = await $.fs.read(`${STATE}/recall-view/${name}.json`)\n      const view = JSON.parse(raw) as { written_at: number; summary: string; lines: string[] }\n      if (view.written_at * 1000 >= startedAt - 2000) {\n        const id = `r${startedAt}`\n        await update($, panels, all => {\n          const kept = Object.fromEntries(Object.entries(all).slice(-30))\n          return { ...kept, [id]: { summary: view.summary, lines: view.lines } }\n        })\n        await $.ui.place({ id })\n      }\n    } catch {\n      // No view this turn (recall off, hook failed): nothing to draw.\n    }\n    return result\n  }).catch(($, e, next) => next(e))\n\n  on('ui.render', { component: 'Placed' }, async ($, e, next) => {\n    if (e.props.plugin !== 'amh-recall-view') return next(e)\n    const panel: RecallPanel | undefined = (await read($, panels))[e.props.id]\n    if (!panel) return next(e)\n    const isOpen = (await read($, open)).includes(e.props.id)\n    const { Box, Text, Button } = $.ui.resolve(e)\n    const [title, ...rest] = panel.summary.split(' · ')\n    const toggle = () =>\n      update($, open, ids => (ids.includes(e.props.id) ? ids.filter(x => x !== e.props.id) : [...ids, e.props.id]))\n    return (\n      <Box flexDirection=\"column\">\n        <Box>\n          <Text color=\"suggestion\" bold>● {title}</Text>\n          <Text>{rest.length ? '(' + rest.join(' · ') + ')' : ''}</Text>\n        </Box>\n        {panel.lines.length > 0 && (\n          <Box flexDirection=\"column\" paddingLeft={2}>\n            {isOpen ? (\n              panel.lines.map(line => <Text>{line}</Text>)\n            ) : (\n              <Text dimColor>⎿  {panel.lines.length} 行明细已收起</Text>\n            )}\n            <Button key=\"toggle\" label={isOpen ? '收起' : '展开'} onPress={toggle} />\n          </Box>\n        )}\n      </Box>\n    )\n  })\n}\n"
TYPES = "export type RecallPanel = { summary: string; lines: string[] }\n\ndeclare module 'claude-code' {\n  interface PluginState {\n    'amh-recall-view': {\n      panels: Shaped<Record<string, RecallPanel>>\n      open: Shaped<string[]>\n    }\n  }\n}\n"


def export_claude_mod(output: Path, state: Path) -> Path:
    """Write the mod folder bound to one client state directory."""
    output = output.expanduser().resolve()
    manifest = {
        "name": NAME,
        "version": "0.1.0",
        "description": "Draws AMH organization-memory recall as a collapsible transcript row",
        "license": "MIT",
        "types": "./types/index.d.ts",
    }
    atomic_text(output / ".claude-plugin" / "plugin.json", json.dumps(manifest, indent=2) + "\n")
    atomic_text(output / "hooks" / "hooks.json", json.dumps({"modules": ["./register.tsx"]}) + "\n")
    bound = REGISTER.replace("__AMH_STATE__", json.dumps(str(state.expanduser().resolve())))
    atomic_text(output / "hooks" / "register.tsx", bound)
    atomic_text(output / "types" / "index.d.ts", TYPES)
    return output
