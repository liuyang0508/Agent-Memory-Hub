from __future__ import annotations

from pathlib import Path

import pytest

from agent_brain.memory.evidence.hook_capture import capture_prompt_payload
from agent_brain.memory.evidence.resource_store import ResourceStore


def _vision_ocr_available() -> bool:
    try:
        import objc  # noqa: F401

        objc.loadBundle("Vision", globals(), bundle_path="/System/Library/Frameworks/Vision.framework")
        return "VNRecognizeTextRequest" in globals()
    except Exception:
        return False


def _write_ocr_fixture(path) -> None:
    Image = pytest.importorskip("PIL.Image")
    ImageDraw = pytest.importorskip("PIL.ImageDraw")
    image = Image.new("RGB", (900, 220), "white")
    draw = ImageDraw.Draw(image)
    draw.text((40, 80), "version.json API_URL failed", fill="black")
    image.save(path)


def _write_pdf_fixture(path, text: str) -> None:
    canvas_module = pytest.importorskip("reportlab.pdfgen.canvas")
    canvas = canvas_module.Canvas(str(path))
    canvas.drawString(72, 720, text)
    canvas.save()


def test_prompt_capture_writes_multimodal_resource_and_payload_caption(tmp_path) -> None:
    from agent_brain.memory.evidence.multimodal_capture import (
        multimodal_gap_payload_for_payload,
        recall_text_for_payload,
    )

    image_path = tmp_path / "runtime-screenshot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    caption = "截图显示 AMH hook 安装脚本读取 version.json 失败。"
    payload = {
        "prompt": "[Image #1]\n我其他同事执行之后有问题",
        "session_id": "sess-mm-caption",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "images": [
            {
                "name": "[Image #1]",
                "path": str(image_path),
                "mime_type": "image/png",
                "caption": caption,
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    store = ResourceStore(tmp_path)
    resources = list(store.iter_resources())
    extractions = list(store.iter_extractions())
    assert len(resources) == 1
    assert resources[0].kind == "image"
    assert resources[0].uri == image_path.resolve().as_uri()
    assert resources[0].metadata["placeholder"] == "[Image #1]"
    assert resources[0].metadata["source_agent"] == "unknown"
    assert len(extractions) == 1
    assert extractions[0].resource_id == resources[0].id
    assert extractions[0].kind == "vlm_caption"
    assert extractions[0].content_text == caption
    assert extractions[0].metadata["extraction_field"] == "caption"

    recall_text = recall_text_for_payload(payload, root_dir=tmp_path)
    assert "AMH hook 安装脚本读取 version.json 失败。" in recall_text
    assert "截图显示" not in recall_text
    assert multimodal_gap_payload_for_payload(payload, root_dir=tmp_path) is None


def test_prompt_capture_records_missing_multimodal_extraction_gap_payload(tmp_path) -> None:
    from agent_brain.memory.evidence.multimodal_capture import (
        multimodal_gap_payload_for_payload,
        recall_text_for_payload,
    )

    payload = {
        "prompt": "[Image #1]\n我其他同事执行之后有问题",
        "session_id": "sess-mm-missing",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    store = ResourceStore(tmp_path)
    resources = list(store.iter_resources())
    assert len(resources) == 1
    assert resources[0].kind == "image"
    assert resources[0].uri.startswith("hook://unknown/UserPromptSubmit/sess-mm-missing#image-1")
    assert resources[0].metadata["extraction_status"] == "missing"
    assert list(store.iter_extractions()) == []
    assert recall_text_for_payload(payload, root_dir=tmp_path) == ""

    gap = multimodal_gap_payload_for_payload(payload, root_dir=tmp_path)
    assert gap is not None
    assert gap["reason"] == "multimodal_extraction_missing"
    assert "multimodal_placeholders=Image#1" in gap["evidence"]


def test_prompt_capture_runs_configured_ocr_for_image_path_without_caption(
    tmp_path,
    monkeypatch,
) -> None:
    from agent_brain.memory.evidence.multimodal_capture import (
        multimodal_gap_payload_for_payload,
        recall_text_for_payload,
    )

    image_path = tmp_path / "ocr-screenshot.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    ocr_script = tmp_path / "fake_ocr.py"
    ocr_script.write_text("print('version.json API_URL failed')\n", encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_HUB_OCR_COMMAND", f"python {ocr_script} {{path}}")
    payload = {
        "prompt": "[Image #1]",
        "session_id": "sess-mm-ocr",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "images": [
            {
                "name": "[Image #1]",
                "path": str(image_path),
                "mime_type": "image/png",
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "ocr"
    assert extractions[0].extractor == "amh.hook.ocr-command"
    assert "version.json" in extractions[0].content_text
    assert "version.json" in recall_text_for_payload(payload, root_dir=tmp_path)
    assert multimodal_gap_payload_for_payload(payload, root_dir=tmp_path) is None


def test_prompt_capture_extracts_pdf_text_from_local_path(tmp_path) -> None:
    from agent_brain.memory.evidence.multimodal_capture import (
        multimodal_gap_payload_for_payload,
        recall_text_for_payload,
    )

    pdf_path = tmp_path / "contract.pdf"
    _write_pdf_fixture(pdf_path, "PDF contract requires SERVER_API_URL build arg")
    payload = {
        "prompt": "[PDF #1]",
        "session_id": "sess-mm-pdf",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "files": [
            {
                "name": "[PDF #1]",
                "path": str(pdf_path),
                "mime_type": "application/pdf",
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "text"
    assert extractions[0].extractor.startswith("amh.hook.local-pdf-text")
    assert "SERVER_API_URL" in extractions[0].content_text
    assert "SERVER_API_URL" in recall_text_for_payload(payload, root_dir=tmp_path)
    assert multimodal_gap_payload_for_payload(payload, root_dir=tmp_path) is None


def test_prompt_capture_extracts_document_text_from_local_path(tmp_path) -> None:
    from agent_brain.memory.evidence.multimodal_capture import recall_text_for_payload

    document_path = tmp_path / "handoff.md"
    document_path.write_text("Document says NEXT_PUBLIC_API_URL must be set.", encoding="utf-8")
    payload = {
        "prompt": "[Document #1]",
        "session_id": "sess-mm-doc",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "files": [
            {
                "name": "[Document #1]",
                "path": str(document_path),
                "mime_type": "text/markdown",
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "text"
    assert extractions[0].extractor == "amh.hook.local-document-text"
    assert "NEXT_PUBLIC_API_URL" in recall_text_for_payload(payload, root_dir=tmp_path)


def test_prompt_capture_does_not_archive_original_resource_by_default(tmp_path, monkeypatch) -> None:
    document_path = tmp_path / "handoff.md"
    document_path.write_text("Default archive mode keeps only locator.", encoding="utf-8")
    monkeypatch.delenv("AGENT_MEMORY_HUB_RESOURCE_ARCHIVE", raising=False)
    payload = {
        "prompt": "[Document #1]",
        "session_id": "sess-mm-no-archive",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "files": [{"name": "[Document #1]", "path": str(document_path), "mime_type": "text/markdown"}],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    resource = next(ResourceStore(tmp_path).iter_resources())
    assert resource.uri == document_path.resolve().as_uri()
    assert "original_uri" not in resource.metadata
    assert not (tmp_path / "blobs").exists()


def test_prompt_capture_archives_original_resource_when_copy_enabled(tmp_path, monkeypatch) -> None:
    from agent_brain.memory.evidence.multimodal_capture import recall_text_for_payload

    document_path = tmp_path / "handoff.md"
    content = "Archived document says SERVER_API_URL must be set."
    document_path.write_text(content, encoding="utf-8")
    monkeypatch.setenv("AGENT_MEMORY_HUB_RESOURCE_ARCHIVE", "copy")
    payload = {
        "prompt": "[Document #1]",
        "session_id": "sess-mm-archive",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "files": [{"name": "[Document #1]", "path": str(document_path), "mime_type": "text/markdown"}],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    resource = next(ResourceStore(tmp_path).iter_resources())
    blob_path = Path(resource.uri.removeprefix("file://"))
    assert blob_path.exists()
    assert blob_path.read_text(encoding="utf-8") == content
    assert blob_path.is_relative_to(tmp_path / "blobs" / "sha256")
    assert resource.metadata["original_uri"] == document_path.resolve().as_uri()
    assert resource.metadata["archive_mode"] == "copy"
    assert resource.metadata["archive_store"] == "blobs/sha256"
    assert "SERVER_API_URL" in recall_text_for_payload(payload, root_dir=tmp_path)


def test_prompt_capture_uses_configured_asr_command_for_audio_path(tmp_path, monkeypatch) -> None:
    from agent_brain.memory.evidence.multimodal_capture import recall_text_for_payload

    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"RIFF....WAVEfmt ")
    asr_script = tmp_path / "fake_asr.py"
    asr_script.write_text(
        "import sys\nprint('Audio transcript says API_URL is missing')\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_MEMORY_HUB_ASR_COMMAND", f"python {asr_script} {{path}}")
    payload = {
        "prompt": "[Audio #1]",
        "session_id": "sess-mm-audio",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "attachments": [
            {
                "name": "[Audio #1]",
                "path": str(audio_path),
                "mime_type": "audio/wav",
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "asr"
    assert extractions[0].extractor == "amh.hook.asr-command"
    assert "API_URL is missing" in recall_text_for_payload(payload, root_dir=tmp_path)


def test_prompt_capture_auto_detects_whisper_asr_command(tmp_path, monkeypatch) -> None:
    from agent_brain.memory.evidence.multimodal_capture import recall_text_for_payload

    audio_path = tmp_path / "meeting.wav"
    audio_path.write_bytes(b"RIFF....WAVEfmt ")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    whisper = bin_dir / "whisper"
    whisper.write_text(
        "#!/bin/sh\necho 'Auto whisper transcript mentions SERVER_API_URL'\n",
        encoding="utf-8",
    )
    whisper.chmod(0o755)
    monkeypatch.delenv("AGENT_MEMORY_HUB_ASR_COMMAND", raising=False)
    monkeypatch.setenv("PATH", f"{bin_dir}")
    payload = {
        "prompt": "[Audio #1]",
        "session_id": "sess-mm-audio-auto",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "attachments": [
            {
                "name": "[Audio #1]",
                "path": str(audio_path),
                "mime_type": "audio/wav",
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    extractions = list(ResourceStore(tmp_path).iter_extractions())
    assert len(extractions) == 1
    assert extractions[0].kind == "asr"
    assert extractions[0].extractor == "amh.hook.asr-auto.whisper"
    assert "SERVER_API_URL" in recall_text_for_payload(payload, root_dir=tmp_path)


def test_prompt_capture_records_missing_for_video_path_without_asr_command(tmp_path) -> None:
    from agent_brain.memory.evidence.multimodal_capture import (
        multimodal_gap_payload_for_payload,
        recall_text_for_payload,
    )

    video_path = tmp_path / "demo.mp4"
    video_path.write_bytes(b"\x00\x00\x00\x18ftypmp42")
    payload = {
        "prompt": "[Video #1]",
        "session_id": "sess-mm-video",
        "cwd": "/repo/current",
        "hook_event_name": "UserPromptSubmit",
        "attachments": [
            {
                "name": "[Video #1]",
                "path": str(video_path),
                "mime_type": "video/mp4",
            }
        ],
    }

    assert capture_prompt_payload(payload, root_dir=tmp_path)

    resources = list(ResourceStore(tmp_path).iter_resources())
    assert len(resources) == 1
    assert resources[0].kind == "video"
    assert resources[0].metadata["extraction_status"] == "missing"
    assert list(ResourceStore(tmp_path).iter_extractions()) == []
    assert recall_text_for_payload(payload, root_dir=tmp_path) == ""
    gap = multimodal_gap_payload_for_payload(payload, root_dir=tmp_path)
    assert gap is not None
    assert gap["reason"] == "multimodal_extraction_missing"
