"""Run hook evidence capture and recall enrichment in one AMH process."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_brain.agent_integrations.runtime_events import record_runtime_event
from agent_brain.memory.context.prompt_normalization import normalize_hook_prompt_for_recall
from agent_brain.memory.evidence.hook_capture import capture_prompt_payload
from agent_brain.memory.evidence.multimodal_capture import (
    capture_multimodal_prompt_resources,
    multimodal_gap_payload_for_payload,
    recall_text_for_payload,
)


PROTOCOL_VERSION = "amh-hook-preflight-v1"
_INVALID_PAYLOAD = object()


@dataclass(frozen=True)
class HookPreflightResult:
    normalized_prompt: str
    multimodal_recall_text: str
    multimodal_gap_json: str


def run_hook_preflight(
    payload: dict[str, Any],
    *,
    brain_dir: Path,
    adapter: str,
) -> HookPreflightResult:
    """Capture independent evidence and return recall inputs for one hook payload."""

    prompt_value = payload.get("prompt", "")
    prompt = prompt_value if isinstance(prompt_value, str) else ""
    normalized_prompt = normalize_hook_prompt_for_recall(prompt)
    event_name = _text(payload.get("hook_event_name")) or "UserPromptSubmit"
    session_id = _text(payload.get("session_id"))
    cwd = _text(payload.get("cwd"))
    effective_adapter = _text(adapter) or "unknown"

    capture_payload = dict(payload)
    capture_payload["prompt"] = prompt
    capture_payload["adapter"] = effective_adapter
    capture_payload["hook_event_name"] = event_name

    try:
        record_runtime_event(
            brain_dir,
            adapter=effective_adapter,
            event_name=event_name,
            session_id=session_id,
            cwd=cwd,
            source="hook",
        )
    except Exception:
        pass

    prompt_captured = False
    try:
        prompt_captured = bool(capture_prompt_payload(capture_payload, root_dir=brain_dir))
    except Exception:
        pass
    if not prompt_captured:
        try:
            capture_multimodal_prompt_resources(capture_payload, root_dir=brain_dir)
        except Exception:
            pass

    multimodal_recall_text = ""
    try:
        recall_text = recall_text_for_payload(capture_payload, root_dir=brain_dir)
        if isinstance(recall_text, str):
            multimodal_recall_text = recall_text
    except Exception:
        pass

    multimodal_gap_json = ""
    try:
        gap_payload = multimodal_gap_payload_for_payload(capture_payload, root_dir=brain_dir)
        if gap_payload is not None:
            multimodal_gap_json = json.dumps(
                gap_payload,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
    except Exception:
        pass

    return HookPreflightResult(
        normalized_prompt=normalized_prompt,
        multimodal_recall_text=multimodal_recall_text,
        multimodal_gap_json=multimodal_gap_json,
    )


def serialize_result(result: HookPreflightResult) -> bytes:
    """Serialize a preflight result as the fixed NUL-delimited hook protocol."""

    fields = (
        PROTOCOL_VERSION,
        result.normalized_prompt,
        result.multimodal_recall_text,
        result.multimodal_gap_json,
    )
    if any("\0" in field for field in fields):
        raise ValueError("NUL byte in protocol field")
    return b"\0".join(field.encode("utf-8") for field in fields) + b"\0"


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _load_payload() -> Any:
    try:
        raw_payload = sys.stdin.buffer.read()
        payload = json.loads(raw_payload)
    except (json.JSONDecodeError, UnicodeDecodeError, ValueError, RecursionError, OSError):
        return _INVALID_PAYLOAD
    return payload


def _fail(message: str) -> int:
    sys.stderr.write(f"hook-preflight: {message}\n")
    return 2


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain-dir", type=Path, required=True)
    parser.add_argument(
        "--adapter",
        default=os.environ.get("AGENT_MEMORY_HUB_ADAPTER") or "unknown",
    )
    args = parser.parse_args(argv)

    payload = _load_payload()
    if payload is _INVALID_PAYLOAD:
        return _fail("invalid JSON")
    if not isinstance(payload, dict):
        return _fail("expected JSON object")

    result = run_hook_preflight(payload, brain_dir=args.brain_dir, adapter=args.adapter)
    try:
        encoded = serialize_result(result)
    except (TypeError, UnicodeEncodeError, ValueError):
        return _fail("invalid protocol field")
    sys.stdout.buffer.write(encoded)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "HookPreflightResult",
    "main",
    "run_hook_preflight",
    "serialize_result",
]
