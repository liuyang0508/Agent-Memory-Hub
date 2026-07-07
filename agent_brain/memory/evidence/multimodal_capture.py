"""Capture prompt-attached multimodal evidence for recall enrichment."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shlex
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from agent_brain.contracts.resource import (
    ExtractionKind,
    ExtractionRecord,
    ResourceKind,
    ResourceRecord,
    make_extraction_id,
    make_resource_id,
    sha256_file,
    sha256_text,
)
from agent_brain.memory.evidence.resource_store import ResourceStore


_PLACEHOLDER_RE = re.compile(
    r"\[(?P<label>Image|Audio|Video|PDF|Document)\s+#(?P<index>\d+)\]",
    re.IGNORECASE,
)
_ATTACHMENT_KEYS = ("attachments", "images", "files", "resources")
_TEXT_FIELDS = (
    ("ocr_text", ExtractionKind.ocr),
    ("caption", ExtractionKind.vlm_caption),
    ("alt_text", ExtractionKind.vlm_caption),
    ("description", ExtractionKind.summary),
    ("transcript", ExtractionKind.asr),
    ("content_text", ExtractionKind.text),
    ("text", ExtractionKind.text),
)
_VISUAL_RECALL_PREFIX_RE = re.compile(
    r"^(?:这(?:张|个)?|该)?(?:截图|图片|图像|画面|屏幕|照片)\s*(?:显示|展示|表明|包含|中有)[：:，,\s]*"
    r"|^(?:the\s+)?(?:screenshot|image|picture|screen)\s+(?:shows?|contains?|depicts?)[:：,\s]*",
    re.IGNORECASE,
)
_TEXT_FILE_SUFFIXES = {
    ".csv",
    ".json",
    ".log",
    ".md",
    ".markdown",
    ".rst",
    ".text",
    ".toml",
    ".txt",
    ".yaml",
    ".yml",
}


@dataclass(frozen=True)
class MultimodalPlaceholder:
    text: str
    label: str
    index: int
    kind: ResourceKind

    @property
    def compact(self) -> str:
        return f"{self.label.capitalize()}#{self.index}"


@dataclass(frozen=True)
class AttachmentCandidate:
    data: dict[str, Any]
    default_kind: ResourceKind | None = None
    ordinal: int = 0


@dataclass(frozen=True)
class ExtractionInput:
    text: str
    field_name: str
    kind: ExtractionKind
    extractor: str = "amh.hook.multimodal-payload"
    extractor_version: str = "1"
    confidence: float = 0.7


@dataclass(frozen=True)
class ExtractionCommand:
    name: str
    parts: list[str]
    append_path: bool = False


def capture_multimodal_prompt_resources(
    payload: dict[str, Any],
    *,
    root_dir: Path | None = None,
) -> list[str]:
    """Write ResourceRecord/ExtractionRecord sidecars for prompt attachments.

    The function accepts best-effort adapter payloads. It never attempts to
    infer image content; only explicit attachment metadata such as caption,
    OCR, transcript, text, path, or URI becomes evidence.
    """
    prompt = _text(payload.get("prompt"))
    placeholders = _placeholders(prompt or "")
    candidates = _attachment_candidates(payload)
    if not placeholders and not candidates:
        return []

    brain_dir = root_dir or _brain_dir()
    store = ResourceStore(brain_dir)
    pairs = _pair_placeholders_and_candidates(placeholders, candidates)
    written: list[str] = []
    for placeholder, candidate in pairs:
        resource, extraction_inputs = _resource_and_extraction_inputs(
            payload=payload,
            placeholder=placeholder,
            candidate=candidate,
            brain_dir=brain_dir,
        )
        try:
            store.write_resource(resource)
        except FileExistsError:
            pass
        else:
            written.append(resource.id)
        for extraction_input in extraction_inputs:
            extraction = ExtractionRecord(
                id=make_extraction_id(f"{resource.title} {extraction_input.field_name}"),
                resource_id=resource.id,
                kind=extraction_input.kind,
                extractor=extraction_input.extractor,
                extractor_version=extraction_input.extractor_version,
                content_text=extraction_input.text,
                content_sha256=sha256_text(extraction_input.text),
                confidence=extraction_input.confidence,
                source_locator=f"{resource.uri}#{extraction_input.field_name}",
                metadata={
                    "evidence_role": "prompt_attachment_extraction",
                    "extraction_field": extraction_input.field_name,
                    "prompt_sha256": _prompt_sha(payload),
                    "session_id": _text(payload.get("session_id")),
                    "hook_event_name": _hook_event(payload),
                    "source_agent": _source_agent(payload),
                },
            )
            try:
                store.write_extraction(extraction)
            except FileExistsError:
                pass
    return written


def recall_text_for_payload(
    payload: dict[str, Any],
    *,
    root_dir: Path | None = None,
    max_chars: int = 4000,
) -> str:
    """Return extraction text captured for this exact prompt payload."""
    if not _has_multimodal_reference(payload):
        return ""
    store = ResourceStore(root_dir or _brain_dir())
    chunks: list[str] = []
    used = 0
    for extraction in store.iter_extractions():
        if not _matches_current_payload(extraction.metadata, payload):
            continue
        text = _recall_query_text(extraction.content_text)
        if not text:
            continue
        if chunks and used + len(text) + 1 > max_chars:
            break
        if not chunks and len(text) > max_chars:
            text = text[:max_chars].rstrip()
        chunks.append(text)
        used += len(text) + 1
    return "\n".join(chunks)


def _recall_query_text(text: str) -> str:
    cleaned = " ".join((text or "").split())
    for _ in range(2):
        stripped = _VISUAL_RECALL_PREFIX_RE.sub("", cleaned).strip()
        if stripped == cleaned:
            break
        cleaned = stripped
    return cleaned


def multimodal_gap_payload_for_payload(
    payload: dict[str, Any],
    *,
    root_dir: Path | None = None,
) -> dict[str, object] | None:
    """Return a recall-gap payload when prompt attachments lack extraction text."""
    prompt = _text(payload.get("prompt")) or ""
    placeholders = _placeholders(prompt)
    if not placeholders and not _attachment_candidates(payload):
        return None
    if recall_text_for_payload(payload, root_dir=root_dir):
        return None
    evidence = [
        "multimodal_placeholders=" + "|".join(p.compact for p in placeholders)
        if placeholders
        else "multimodal_payload_without_placeholder",
        "extraction_text=missing",
    ]
    resources = [
        resource.id
        for resource in ResourceStore(root_dir or _brain_dir()).iter_resources()
        if _metadata_matches_payload(resource.metadata, payload)
    ]
    if resources:
        evidence.append("resources=" + "|".join(resources[:5]))
    return {
        "reason": "multimodal_extraction_missing",
        "evidence": evidence,
    }


def _resource_and_extraction_inputs(
    *,
    payload: dict[str, Any],
    placeholder: MultimodalPlaceholder | None,
    candidate: AttachmentCandidate | None,
    brain_dir: Path,
) -> tuple[ResourceRecord, list[ExtractionInput]]:
    data = candidate.data if candidate else {}
    kind = _resource_kind(placeholder, candidate)
    title = _title(data, placeholder, candidate)
    uri, mime_type, file_sha, size_bytes = _resource_locator(data, kind, payload, placeholder, candidate)
    extraction_inputs = _extraction_inputs(data, kind)
    metadata = {
        "evidence_role": "prompt_attachment",
        "prompt_sha256": _prompt_sha(payload),
        "source_agent": _source_agent(payload),
        "session_id": _text(payload.get("session_id")),
        "hook_event_name": _hook_event(payload),
        "cwd": _text(payload.get("cwd")),
        "placeholder": placeholder.text if placeholder else None,
        "placeholder_index": placeholder.index if placeholder else None,
        "attachment_ordinal": candidate.ordinal if candidate else None,
        "extraction_status": "available" if extraction_inputs else "missing",
    }
    metadata.update(_selected_candidate_metadata(data))
    resource = ResourceRecord(
        id=make_resource_id(title),
        kind=kind,
        uri=uri,
        title=title,
        mime_type=mime_type,
        sha256=file_sha,
        size_bytes=size_bytes,
        project=_project(payload),
        tags=_resource_tags(kind),
        metadata=metadata,
    )
    resource = _archive_resource_if_requested(resource, data=data, brain_dir=brain_dir)
    return resource, extraction_inputs


def _placeholders(prompt: str) -> list[MultimodalPlaceholder]:
    placeholders: list[MultimodalPlaceholder] = []
    for match in _PLACEHOLDER_RE.finditer(prompt):
        label = match.group("label")
        placeholders.append(
            MultimodalPlaceholder(
                text=match.group(0),
                label=label,
                index=int(match.group("index")),
                kind=_kind_from_label(label),
            )
        )
    return placeholders


def _attachment_candidates(payload: dict[str, Any]) -> list[AttachmentCandidate]:
    candidates: list[AttachmentCandidate] = []
    for key in _ATTACHMENT_KEYS:
        value = payload.get(key)
        if value is None:
            continue
        default_kind = _default_kind_for_key(key)
        values = value if isinstance(value, list) else [value]
        for raw in values:
            data = raw if isinstance(raw, dict) else {"path": raw}
            candidates.append(
                AttachmentCandidate(
                    data=dict(data),
                    default_kind=default_kind,
                    ordinal=len(candidates) + 1,
                )
            )
    return candidates


def _pair_placeholders_and_candidates(
    placeholders: list[MultimodalPlaceholder],
    candidates: list[AttachmentCandidate],
) -> list[tuple[MultimodalPlaceholder | None, AttachmentCandidate | None]]:
    pairs: list[tuple[MultimodalPlaceholder | None, AttachmentCandidate | None]] = []
    used: set[int] = set()
    for position, placeholder in enumerate(placeholders):
        matched = _matching_candidate(placeholder, candidates, used, position)
        if matched is not None:
            used.add(matched.ordinal)
        pairs.append((placeholder, matched))
    for candidate in candidates:
        if candidate.ordinal not in used:
            pairs.append((None, candidate))
    return pairs


def _matching_candidate(
    placeholder: MultimodalPlaceholder,
    candidates: list[AttachmentCandidate],
    used: set[int],
    position: int,
) -> AttachmentCandidate | None:
    for candidate in candidates:
        if candidate.ordinal in used:
            continue
        values = {
            _text(candidate.data.get("name")),
            _text(candidate.data.get("label")),
            _text(candidate.data.get("placeholder")),
            _text(candidate.data.get("ref")),
            _text(candidate.data.get("id")),
        }
        if placeholder.text in values:
            return candidate
        index = candidate.data.get("index") or candidate.data.get("number")
        if str(index or "") == str(placeholder.index):
            return candidate
    ordered = [candidate for candidate in candidates if candidate.ordinal not in used]
    if len(ordered) > position:
        return ordered[position]
    return None


def _resource_kind(
    placeholder: MultimodalPlaceholder | None,
    candidate: AttachmentCandidate | None,
) -> ResourceKind:
    data = candidate.data if candidate else {}
    declared = _text(data.get("kind")) or _text(data.get("type"))
    if declared:
        lowered = declared.lower()
        if lowered in {kind.value for kind in ResourceKind}:
            return ResourceKind(lowered)
    mime_type = _text(data.get("mime_type")) or _text(data.get("mime"))
    if mime_type:
        if mime_type.startswith("image/"):
            return ResourceKind.image
        if mime_type == "application/pdf":
            return ResourceKind.pdf
        if mime_type.startswith("audio/"):
            return ResourceKind.audio
        if mime_type.startswith("video/"):
            return ResourceKind.video
        if mime_type.startswith("text/"):
            return ResourceKind.document
    path_or_uri = _text(data.get("path")) or _text(data.get("file_path")) or _text(data.get("uri"))
    if path_or_uri:
        guessed, _encoding = mimetypes.guess_type(path_or_uri)
        if guessed:
            return _resource_kind(placeholder, AttachmentCandidate({"mime_type": guessed}))
    if placeholder:
        return placeholder.kind
    if candidate and candidate.default_kind:
        return candidate.default_kind
    return ResourceKind.other


def _resource_locator(
    data: dict[str, Any],
    kind: ResourceKind,
    payload: dict[str, Any],
    placeholder: MultimodalPlaceholder | None,
    candidate: AttachmentCandidate | None,
) -> tuple[str, str | None, str | None, int | None]:
    path_text = (
        _text(data.get("path"))
        or _text(data.get("file_path"))
        or _text(data.get("local_path"))
    )
    mime_type = _text(data.get("mime_type")) or _text(data.get("mime"))
    if path_text:
        path = Path(path_text).expanduser()
        guessed, _encoding = mimetypes.guess_type(str(path))
        mime_type = mime_type or guessed
        if path.exists() and path.is_file():
            stat = path.stat()
            return _file_uri(path), mime_type, sha256_file(path), stat.st_size
        return str(path), mime_type, None, None
    uri = _text(data.get("uri")) or _text(data.get("url")) or _text(data.get("href"))
    if uri:
        return uri, mime_type, _text(data.get("sha256")), _int_or_none(data.get("size_bytes"))
    return _synthetic_uri(payload, kind, placeholder, candidate), mime_type, None, None


def _archive_resource_if_requested(
    resource: ResourceRecord,
    *,
    data: dict[str, Any],
    brain_dir: Path,
) -> ResourceRecord:
    mode = os.environ.get("AGENT_MEMORY_HUB_RESOURCE_ARCHIVE", "off").strip().lower()
    if mode in {"", "0", "false", "no", "off"}:
        return resource
    if mode != "copy":
        return resource
    path = _attachment_file_path(data)
    if path is None or not path.exists() or not path.is_file():
        return resource
    sha = resource.sha256 or sha256_file(path)
    blob_path = _blob_path(brain_dir, sha)
    try:
        blob_path.parent.mkdir(parents=True, exist_ok=True)
        if not blob_path.exists():
            shutil.copy2(path, blob_path)
    except OSError:
        return resource
    metadata = dict(resource.metadata)
    metadata.update({
        "original_uri": resource.uri,
        "archive_mode": "copy",
        "archive_store": "blobs/sha256",
        "archive_sha256": sha,
    })
    return resource.model_copy(update={
        "uri": _file_uri(blob_path),
        "sha256": sha,
        "metadata": metadata,
    })


def _blob_path(brain_dir: Path, sha: str) -> Path:
    return Path(brain_dir) / "blobs" / "sha256" / sha[:2] / sha


def _extraction_inputs(
    data: dict[str, Any],
    kind: ResourceKind,
) -> list[ExtractionInput]:
    inputs: list[ExtractionInput] = []
    for field_name, extraction_kind in _TEXT_FIELDS:
        text = _text(data.get(field_name))
        if not text:
            continue
        if field_name == "transcript" and kind not in {ResourceKind.audio, ResourceKind.video}:
            extraction_kind = ExtractionKind.text
        if field_name in {"caption", "alt_text"} and kind not in {ResourceKind.image, ResourceKind.video}:
            extraction_kind = ExtractionKind.summary
        inputs.append(
            ExtractionInput(
                text=text,
                field_name=field_name,
                kind=extraction_kind,
                confidence=_confidence(data),
            )
        )
    if not inputs and kind == ResourceKind.image:
        ocr_result = _local_image_ocr_result(data)
        if ocr_result:
            ocr_text, extractor, extractor_version = ocr_result
            inputs.append(
                ExtractionInput(
                    text=ocr_text,
                    field_name="vision_ocr",
                    kind=ExtractionKind.ocr,
                    extractor=extractor,
                    extractor_version=extractor_version,
                    confidence=0.72,
                )
            )
    if not inputs and kind == ResourceKind.pdf:
        pdf_text = _local_pdf_text(data)
        if pdf_text:
            inputs.append(
                ExtractionInput(
                    text=pdf_text,
                    field_name="pdf_text",
                    kind=ExtractionKind.text,
                    extractor=_pdf_text_extractor_name(),
                    extractor_version="1",
                    confidence=0.78,
                )
            )
    if not inputs and kind == ResourceKind.document:
        document_text = _local_document_text(data)
        if document_text:
            inputs.append(
                ExtractionInput(
                    text=document_text,
                    field_name="document_text",
                    kind=ExtractionKind.text,
                    extractor="amh.hook.local-document-text",
                    extractor_version="1",
                    confidence=0.82,
                )
            )
    if not inputs and kind in {ResourceKind.audio, ResourceKind.video}:
        asr_result = _local_asr_text(data)
        if asr_result:
            transcript, extractor = asr_result
            inputs.append(
                ExtractionInput(
                    text=transcript,
                    field_name="asr_command",
                    kind=ExtractionKind.asr,
                    extractor=extractor,
                    extractor_version="1",
                    confidence=0.68,
                )
            )
    return inputs


def _local_image_ocr_result(data: dict[str, Any]) -> tuple[str, str, str] | None:
    if not _local_ocr_enabled():
        return None
    path = _attachment_file_path(data)
    if path is None or not path.exists() or not path.is_file():
        return None
    max_bytes = _local_ocr_max_bytes()
    try:
        if path.stat().st_size > max_bytes:
            return None
    except OSError:
        return None
    explicit = _text(os.environ.get("AGENT_MEMORY_HUB_OCR_COMMAND"))
    if explicit:
        text = _run_extraction_command(
            ExtractionCommand(
                "command",
                _extraction_command_parts(explicit),
                append_path=_extraction_command_needs_path(explicit),
            ),
            path,
            data,
            timeout_seconds=_local_ocr_timeout_seconds(),
        )
        if text:
            return text, "amh.hook.ocr-command", "1"
    text = _macos_vision_ocr(path)
    return (text, "amh.hook.vision-ocr", "macos-vision") if text else None


def _local_pdf_text(data: dict[str, Any]) -> str | None:
    if not _local_pdf_text_enabled():
        return None
    path = _attachment_file_path(data)
    if path is None or not path.exists() or not path.is_file():
        return None
    if _file_too_large(path, _local_pdf_text_max_bytes()):
        return None
    text = _pdftotext_text(path)
    if text:
        return text
    return _pypdf_text(path)


def _local_document_text(data: dict[str, Any]) -> str | None:
    if not _local_document_text_enabled():
        return None
    path = _attachment_file_path(data)
    if path is None or not path.exists() or not path.is_file():
        return None
    if _file_too_large(path, _local_document_text_max_bytes()):
        return None
    text = _read_text_document(path)
    if text:
        return text
    return _textutil_text(path)


def _local_asr_text(data: dict[str, Any]) -> tuple[str, str] | None:
    path = _attachment_file_path(data)
    if path is None or not path.exists() or not path.is_file():
        return None
    if _file_too_large(path, _local_asr_max_bytes()):
        return None
    explicit = _text(os.environ.get("AGENT_MEMORY_HUB_ASR_COMMAND"))
    if explicit:
        text = _run_asr_command(
            ExtractionCommand(
                "command",
                _extraction_command_parts(explicit),
                append_path=_extraction_command_needs_path(explicit),
            ),
            path,
            data,
        )
        return (text, "amh.hook.asr-command") if text else None
    for command in _auto_asr_commands():
        text = _run_asr_command(command, path, data)
        if text:
            return text, f"amh.hook.asr-auto.{command.name}"
    return None


def _attachment_file_path(data: dict[str, Any]) -> Path | None:
    path_text = (
        _text(data.get("path"))
        or _text(data.get("file_path"))
        or _text(data.get("local_path"))
    )
    return Path(path_text).expanduser() if path_text else None


def _pdftotext_text(path: Path) -> str | None:
    tool = shutil.which(os.environ.get("AGENT_MEMORY_HUB_PDFTOTEXT") or "pdftotext")
    if not tool:
        return None
    try:
        proc = subprocess.run(
            [tool, "-layout", "-enc", "UTF-8", str(path), "-"],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_local_pdf_text_timeout_seconds(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if proc.returncode != 0:
        return None
    return _bounded_text(proc.stdout, _local_extraction_max_chars())


def _pypdf_text(path: Path) -> str | None:
    try:
        from pypdf import PdfReader

        reader = PdfReader(str(path))
        chunks: list[str] = []
        for page in reader.pages[:_local_pdf_max_pages()]:
            text = page.extract_text() or ""
            if text.strip():
                chunks.append(text)
        return _bounded_text("\n".join(chunks), _local_extraction_max_chars())
    except Exception:
        return None


def _read_text_document(path: Path) -> str | None:
    if path.suffix.lower() not in _TEXT_FILE_SUFFIXES:
        return None
    for encoding in ("utf-8", "utf-8-sig"):
        try:
            return _bounded_text(path.read_text(encoding=encoding), _local_extraction_max_chars())
        except UnicodeDecodeError:
            continue
        except OSError:
            return None
    return None


def _textutil_text(path: Path) -> str | None:
    tool = shutil.which("textutil")
    if not tool:
        return None
    try:
        proc = subprocess.run(
            [tool, "-convert", "txt", "-stdout", str(path)],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=_local_document_text_timeout_seconds(),
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None
    if proc.returncode != 0:
        return None
    return _bounded_text(proc.stdout, _local_extraction_max_chars())


def _run_asr_command(command: ExtractionCommand, path: Path, data: dict[str, Any]) -> str | None:
    return _run_extraction_command(
        command,
        path,
        data,
        timeout_seconds=_local_asr_timeout_seconds(),
    )


def _run_extraction_command(
    command: ExtractionCommand,
    path: Path,
    data: dict[str, Any],
    *,
    timeout_seconds: float,
) -> str | None:
    if not command.parts:
        return None
    executable = command.parts[0]
    if "/" not in executable:
        executable = shutil.which(executable) or executable
    if not shutil.which(executable) and "/" not in executable:
        return None
    uri = _text(data.get("uri")) or _text(data.get("url")) or _file_uri(path)
    expanded = [
        part.replace("{path}", str(path)).replace("{uri}", uri).replace("{file}", str(path))
        for part in [executable, *command.parts[1:]]
    ]
    if command.append_path:
        expanded.append(str(path))
    try:
        proc = subprocess.run(
            expanded,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError):
        return None
    if proc.returncode != 0:
        return None
    return _bounded_text(proc.stdout, _local_extraction_max_chars())


def _extraction_command_parts(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _extraction_command_needs_path(command: str) -> bool:
    return "{path}" not in command and "{file}" not in command and "{uri}" not in command


def _auto_asr_commands() -> list[ExtractionCommand]:
    commands: list[ExtractionCommand] = []

    def add(name: str, parts: list[str], *, append_path: bool = False) -> None:
        if parts and shutil.which(parts[0]):
            commands.append(ExtractionCommand(name, parts, append_path=append_path))

    add("whisper", [
        "whisper",
        "{path}",
        "--model",
        os.environ.get("AGENT_MEMORY_HUB_ASR_WHISPER_MODEL", "base"),
        "--output_format",
        "txt",
        "--output_dir",
        "-",
    ])
    add("faster-whisper", ["faster-whisper", "{path}"])
    add("insanely-fast-whisper", ["insanely-fast-whisper", "--file-name", "{path}"])
    add("parakeet-mlx", ["parakeet-mlx", "{path}"])
    add("parakeet", ["parakeet", "{path}"])
    add("whisper-cli", ["whisper-cli", "-f", "{path}", "-otxt", "-of", "-"])
    add("whisper-cpp", ["whisper-cpp", "-f", "{path}", "-otxt", "-of", "-"])
    return commands


def _bounded_text(text: str, max_chars: int) -> str | None:
    cleaned = "\n".join(line.rstrip() for line in (text or "").splitlines()).strip()
    if not cleaned:
        return None
    return cleaned[:max_chars].rstrip()


def _file_too_large(path: Path, max_bytes: int) -> bool:
    try:
        return path.stat().st_size > max_bytes
    except OSError:
        return True


def _local_ocr_enabled() -> bool:
    value = os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_OCR", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _local_pdf_text_enabled() -> bool:
    value = os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_PDF_TEXT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _local_document_text_enabled() -> bool:
    value = os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_DOCUMENT_TEXT", "1").strip().lower()
    return value not in {"0", "false", "no", "off"}


def _local_ocr_max_bytes() -> int:
    try:
        return int(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_OCR_MAX_BYTES", str(8 * 1024 * 1024)))
    except ValueError:
        return 8 * 1024 * 1024


def _local_pdf_text_max_bytes() -> int:
    try:
        return int(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_PDF_MAX_BYTES", str(32 * 1024 * 1024)))
    except ValueError:
        return 32 * 1024 * 1024


def _local_document_text_max_bytes() -> int:
    try:
        return int(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_DOCUMENT_MAX_BYTES", str(4 * 1024 * 1024)))
    except ValueError:
        return 4 * 1024 * 1024


def _local_asr_max_bytes() -> int:
    try:
        return int(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_ASR_MAX_BYTES", str(256 * 1024 * 1024)))
    except ValueError:
        return 256 * 1024 * 1024


def _local_extraction_max_chars() -> int:
    try:
        return int(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_EXTRACTION_MAX_CHARS", "12000"))
    except ValueError:
        return 12000


def _local_pdf_max_pages() -> int:
    try:
        return int(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_PDF_MAX_PAGES", "20"))
    except ValueError:
        return 20


def _local_pdf_text_timeout_seconds() -> float:
    try:
        return float(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_PDF_TIMEOUT_SECONDS", "6"))
    except ValueError:
        return 6.0


def _local_document_text_timeout_seconds() -> float:
    try:
        return float(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_DOCUMENT_TIMEOUT_SECONDS", "4"))
    except ValueError:
        return 4.0


def _local_asr_timeout_seconds() -> float:
    try:
        return float(os.environ.get("AGENT_MEMORY_HUB_ASR_TIMEOUT_SECONDS", "30"))
    except ValueError:
        return 30.0


def _pdf_text_extractor_name() -> str:
    if shutil.which(os.environ.get("AGENT_MEMORY_HUB_PDFTOTEXT") or "pdftotext"):
        return "amh.hook.local-pdf-text.pdftotext"
    return "amh.hook.local-pdf-text.pypdf"


def _macos_vision_ocr(path: Path) -> str | None:
    text = _macos_vision_ocr_in_process(path)
    if text:
        return text
    return _macos_vision_ocr_subprocess(path)


def _macos_vision_ocr_in_process(path: Path) -> str | None:
    try:
        import objc
        from Foundation import NSURL

        namespace: dict[str, Any] = {}
        objc.loadBundle(
            "Vision",
            namespace,
            bundle_path="/System/Library/Frameworks/Vision.framework",
        )
        request_cls = namespace.get("VNRecognizeTextRequest")
        handler_cls = namespace.get("VNImageRequestHandler")
        if request_cls is None or handler_cls is None:
            return None
        request = request_cls.alloc().init()
        if hasattr(request, "setRecognitionLevel_"):
            request.setRecognitionLevel_(0)
        if hasattr(request, "setUsesLanguageCorrection_"):
            request.setUsesLanguageCorrection_(True)
        url = NSURL.fileURLWithPath_(str(path))
        handler = handler_cls.alloc().initWithURL_options_(url, {})
        if not handler.performRequests_error_([request], None):
            return None
        lines: list[str] = []
        for observation in request.results() or []:
            candidates = observation.topCandidates_(1)
            if candidates:
                text = _text(candidates[0].string())
                if text:
                    lines.append(text)
        return "\n".join(lines) if lines else None
    except Exception:
        return None


def _macos_vision_ocr_subprocess(path: Path) -> str | None:
    script = r'''
import json
import sys

try:
    import objc
    from Foundation import NSURL

    namespace = {}
    objc.loadBundle(
        "Vision",
        namespace,
        bundle_path="/System/Library/Frameworks/Vision.framework",
    )
    request_cls = namespace.get("VNRecognizeTextRequest")
    handler_cls = namespace.get("VNImageRequestHandler")
    if request_cls is None or handler_cls is None:
        raise SystemExit(2)
    request = request_cls.alloc().init()
    if hasattr(request, "setRecognitionLevel_"):
        request.setRecognitionLevel_(0)
    if hasattr(request, "setUsesLanguageCorrection_"):
        request.setUsesLanguageCorrection_(True)
    handler = handler_cls.alloc().initWithURL_options_(NSURL.fileURLWithPath_(sys.argv[1]), {})
    if not handler.performRequests_error_([request], None):
        raise SystemExit(3)
    lines = []
    for observation in request.results() or []:
        candidates = observation.topCandidates_(1)
        if candidates:
            text = str(candidates[0].string()).strip()
            if text:
                lines.append(text)
    print(json.dumps({"text": "\n".join(lines)}, ensure_ascii=False))
except Exception:
    raise SystemExit(1)
'''
    for python in _vision_python_candidates():
        try:
            proc = subprocess.run(
                [python, "-c", script, str(path)],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=_local_ocr_timeout_seconds(),
            )
        except (OSError, subprocess.TimeoutExpired):
            continue
        if proc.returncode != 0 or not proc.stdout.strip():
            continue
        try:
            payload = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        text = _text(payload.get("text"))
        if text:
            return text
    return None


def _vision_python_candidates() -> list[str]:
    candidates: list[str] = []

    def add(value: str | None) -> None:
        if not value:
            return
        resolved = shutil.which(value) if "/" not in value else value
        if not resolved:
            return
        if resolved == sys.executable:
            return
        if resolved not in candidates:
            candidates.append(resolved)

    add(os.environ.get("AGENT_MEMORY_HUB_VISION_PYTHON"))
    for name in ("python", "python3", "python3.14", "python3.13", "python3.12", "python3.11"):
        add(name)
    return candidates


def _local_ocr_timeout_seconds() -> float:
    try:
        return float(os.environ.get("AGENT_MEMORY_HUB_MULTIMODAL_OCR_TIMEOUT_SECONDS", "6"))
    except ValueError:
        return 6.0


def _metadata_matches_payload(metadata: dict[str, Any], payload: dict[str, Any]) -> bool:
    return (
        str(metadata.get("prompt_sha256") or "") == _prompt_sha(payload)
        and str(metadata.get("source_agent") or "") == _source_agent(payload)
        and str(metadata.get("hook_event_name") or "") == _hook_event(payload)
        and (metadata.get("session_id") or None) == (_text(payload.get("session_id")) or None)
    )


def _matches_current_payload(metadata: dict[str, Any], payload: dict[str, Any]) -> bool:
    return _metadata_matches_payload(metadata, payload)


def _selected_candidate_metadata(data: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    for key in ("name", "label", "id", "path", "file_path", "uri", "url", "sha256"):
        value = _text(data.get(key))
        if value:
            metadata[f"attachment_{key}"] = value
    return metadata


def _title(
    data: dict[str, Any],
    placeholder: MultimodalPlaceholder | None,
    candidate: AttachmentCandidate | None,
) -> str:
    explicit = _text(data.get("title")) or _text(data.get("name")) or _text(data.get("label"))
    if explicit:
        return explicit.strip("[]") or "Prompt attachment"
    if placeholder:
        return f"Prompt {placeholder.label.capitalize()} {placeholder.index}"
    if candidate:
        return f"Prompt Attachment {candidate.ordinal}"
    return "Prompt Attachment"


def _synthetic_uri(
    payload: dict[str, Any],
    kind: ResourceKind,
    placeholder: MultimodalPlaceholder | None,
    candidate: AttachmentCandidate | None,
) -> str:
    agent = _source_agent(payload)
    event = _hook_event(payload)
    session = _text(payload.get("session_id")) or "unknown-session"
    if placeholder:
        anchor = f"{kind.value}-{placeholder.index}"
    elif candidate:
        anchor = f"{kind.value}-{candidate.ordinal}"
    else:
        anchor = kind.value
    return f"hook://{agent}/{event}/{session}#{anchor}"


def _resource_tags(kind: ResourceKind) -> list[str]:
    return ["hook", "prompt-attachment", f"modality:{kind.value}"]


def _kind_from_label(label: str) -> ResourceKind:
    lowered = label.lower()
    if lowered == "image":
        return ResourceKind.image
    if lowered == "audio":
        return ResourceKind.audio
    if lowered == "video":
        return ResourceKind.video
    if lowered == "pdf":
        return ResourceKind.pdf
    if lowered == "document":
        return ResourceKind.document
    return ResourceKind.other


def _default_kind_for_key(key: str) -> ResourceKind | None:
    if key == "images":
        return ResourceKind.image
    if key == "files":
        return ResourceKind.file
    return None


def _has_multimodal_reference(payload: dict[str, Any]) -> bool:
    return bool(_placeholders(_text(payload.get("prompt")) or "") or _attachment_candidates(payload))


def _prompt_sha(payload: dict[str, Any]) -> str:
    return sha256_text(_text(payload.get("prompt")) or "")


def _source_agent(payload: dict[str, Any]) -> str:
    return (
        _text(os.environ.get("AGENT_MEMORY_HUB_ADAPTER"))
        or _text(payload.get("adapter"))
        or _text(payload.get("source_agent"))
        or "unknown"
    )


def _project(payload: dict[str, Any]) -> str | None:
    return _text(os.environ.get("AGENT_MEMORY_HUB_PROJECT")) or _text(payload.get("project"))


def _hook_event(payload: dict[str, Any]) -> str:
    return _text(payload.get("hook_event_name")) or "UserPromptSubmit"


def _confidence(data: dict[str, Any]) -> float:
    try:
        value = float(data.get("confidence", 0.7))
    except (TypeError, ValueError):
        return 0.7
    return min(1.0, max(0.0, value))


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _file_uri(path: Path) -> str:
    try:
        return path.resolve(strict=False).as_uri()
    except ValueError:
        return str(path)


def _brain_dir() -> Path:
    return Path(os.environ.get("BRAIN_DIR", "~/.agent-memory-hub")).expanduser()


def _load_payload() -> dict[str, Any]:
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, OSError):
        return {}
    return payload if isinstance(payload, dict) else {}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("capture", "recall-text", "gap-json"))
    args = parser.parse_args(argv)
    payload = _load_payload()
    if args.mode == "capture":
        capture_multimodal_prompt_resources(payload)
        return 0
    if args.mode == "recall-text":
        sys.stdout.write(recall_text_for_payload(payload))
        return 0
    gap = multimodal_gap_payload_for_payload(payload)
    if gap:
        sys.stdout.write(json.dumps(gap, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "capture_multimodal_prompt_resources",
    "main",
    "multimodal_gap_payload_for_payload",
    "recall_text_for_payload",
]
