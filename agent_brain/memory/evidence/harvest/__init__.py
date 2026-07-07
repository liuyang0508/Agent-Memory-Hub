"""Transcript harvester package (Stage A).

Offline-first reconstruction of memory from durable Claude Code transcripts.
Submodules:
    transcript_reader — stream CC jsonl into text spans with byte offsets.
    watermark         — per-transcript resumable harvest state.
    extractor / dedup — mechanical distillation + span-level dedup.
    harvester         — orchestrator that writes via the shared WriteService.
    enricher          — optional, strictly non-blocking LLM upgrade.

Everything here must work with no network and no model; the LLM enricher is the
only optional upgrade and degrades to a clean no-op when unavailable.
"""
