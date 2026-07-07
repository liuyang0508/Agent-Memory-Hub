"""Mechanical (zero-model) distillation of transcript spans into raw candidates.

Conservative regex/keyword rules surface decisions, error→fix episodes, and
artifacts. Output is always abstraction=L0 (raw), low confidence — the optional
LLM enricher upgrades later. Secrets are redacted before anything is emitted.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

from agent_brain.memory.evidence.harvest.transcript_reader import TranscriptSpan
from agent_brain.memory.evidence.harvest.dedup import span_hash

_SECRET_RE = re.compile(r"(AKIA[0-9A-Z]{16}|-----BEGIN [A-Z ]*PRIVATE KEY-----|ghp_[A-Za-z0-9]{36})")
_DECISION_RE = re.compile(r"\b(decision|chose|decided|选|采用|改用|决定|决策)\b", re.I)
_FACT_RE = re.compile(r"\b(fact|事实)\b", re.I)
_FIX_RE = re.compile(r"\b(failed|error|fixed|root cause|因为|修复)\b", re.I)


@dataclass
class Candidate:
    type: str
    title: str
    summary: str
    body: str
    abstraction: str = "L0"
    confidence: float = 0.4
    span_hash: str = ""
    tags: list[str] = field(default_factory=list)


def _redact(text: str) -> str:
    return _SECRET_RE.sub("«REDACTED»", text)


def extract_candidates(spans: list[TranscriptSpan]) -> list[Candidate]:
    out: list[Candidate] = []
    for s in spans:
        body = _redact(s.text.strip())
        if not body:
            continue
        title = (body.splitlines()[0])[:80]
        h = span_hash(s.text)
        if _DECISION_RE.search(body):
            out.append(Candidate("decision", title, body[:200], body, span_hash=h, tags=["harvested", "decision"]))
        elif _FACT_RE.search(body):
            out.append(Candidate("fact", title, body[:200], body, span_hash=h, tags=["harvested", "fact"]))
        elif _FIX_RE.search(body):
            out.append(Candidate("episode", title, body[:200], body, span_hash=h, tags=["harvested", "episode"]))
    return out
