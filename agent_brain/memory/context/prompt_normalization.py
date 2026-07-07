"""Normalize hook prompts before memory recall.

Some clients pass a composite prompt to UserPromptSubmit hooks: the real user
message plus client system reminders, injected memory candidates, or workspace
metadata.  Recall should be driven by the user intent, while raw prompt capture
still records the original payload as evidence.
"""

from __future__ import annotations

import re
import sys
from urllib.parse import unquote, urlsplit

_REMOVABLE_BLOCKS = (
    re.compile(r"<system-reminder\b[^>]*>.*?(?:</system-reminder>|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"<agent_brain\b[^>]*>.*?(?:</agent_brain>|$)", re.IGNORECASE | re.DOTALL),
    re.compile(r"<agent-brain\b[^>]*>.*?(?:</agent-brain>|$)", re.IGNORECASE | re.DOTALL),
)

_NOISY_LINE_PREFIXES = (
    "available mcp servers:",
    "available tools:",
    "current workspace may include",
)

_INLINE_AGENT_INSTRUCTION_PATTERNS = (
    re.compile(
        r"请(?:优先|只)?(?:根据|基于)[^。！？!?]*"
        r"(?:自动注入|memory\s+candidates?|<agent_brain>|系统上下文)[^。！？!?]*"
        r"(?:回答|答复)[，,。.!！?？]*",
        re.IGNORECASE,
    ),
    re.compile(r"不要调用工具[，,。.!！?？]*", re.IGNORECASE),
)

_MULTIMODAL_PLACEHOLDER_RE = re.compile(
    r"\[(?:Image|Audio|Video|PDF|Document)\s+#\d+\]",
    re.IGNORECASE,
)
_FILE_URI_RE = re.compile(
    r"file://[A-Za-z0-9:/._~%+#?=&@!$&'()*+,;=-]+",
    re.IGNORECASE,
)


def normalize_hook_prompt_for_recall(prompt: str) -> str:
    """Return the user-intent slice used by recall/search gates."""

    text = (prompt or "").replace("\r\n", "\n").replace("\r", "\n")
    for pattern in _REMOVABLE_BLOCKS:
        text = pattern.sub("", text)
    for pattern in _INLINE_AGENT_INSTRUCTION_PATTERNS:
        text = pattern.sub("", text)
    text = _MULTIMODAL_PLACEHOLDER_RE.sub("", text)
    text = _FILE_URI_RE.sub(_summarize_file_uri_for_recall, text)

    lines: list[str] = []
    for raw_line in text.split("\n"):
        line = raw_line.rstrip()
        if not line.strip():
            lines.append("")
            continue
        lower = line.strip().lower()
        if any(lower.startswith(prefix) for prefix in _NOISY_LINE_PREFIXES):
            continue
        lines.append(line)

    return _collapse_blank_lines("\n".join(lines)).strip()


def _summarize_file_uri_for_recall(match: re.Match[str]) -> str:
    """Keep useful file anchors without leaking local directory path segments."""

    uri = match.group(0)
    parsed = urlsplit(uri)
    filename = unquote(parsed.path.rsplit("/", 1)[-1])
    fragment = unquote(parsed.fragment)
    anchors = [value for value in (filename, fragment) if value]
    if not anchors:
        return " "
    return " " + " ".join(anchors) + " "


def _collapse_blank_lines(text: str) -> str:
    cleaned: list[str] = []
    previous_blank = False
    for line in text.split("\n"):
        blank = not line.strip()
        if blank and previous_blank:
            continue
        cleaned.append(line)
        previous_blank = blank
    return "\n".join(cleaned)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    prompt = args[0] if args else sys.stdin.read()
    sys.stdout.write(normalize_hook_prompt_for_recall(prompt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["normalize_hook_prompt_for_recall"]
