"""Audit governance routes for the Web Admin API."""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from web._base import _audit, _state_store
from web.auth import CurrentUser, get_current_user


router = APIRouter()


@router.get("/api/audit")
async def get_audit_log(
    limit: int = Query(50, le=500),
    user: CurrentUser = Depends(get_current_user),
):
    """Return recent audit log entries (admin only)."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    entries, total = _state_store().list_audit(limit)
    return {"entries": entries, "total": total}


class AuditScanRequest(BaseModel):
    path: str
    glob: str = "**/*"


@router.post("/api/audit/scan")
async def audit_scan(req: AuditScanRequest, user: CurrentUser = Depends(get_current_user)):
    """Run skill audit scanner on a directory path."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    from agent_brain.memory.governance.audit.scanner import SkillScanner
    from agent_brain.memory.governance.audit.rules import load_builtin_rules

    target = Path(req.path).expanduser()
    if not target.exists():
        raise HTTPException(status_code=404, detail="path not found")
    scanner = SkillScanner(rules=load_builtin_rules())
    report = scanner.scan_directory(target, glob=req.glob)
    _audit(user.username, "audit_scan", f"{target}: {report.total_findings} findings")
    return {
        "scanned_files": report.scanned_files,
        "total_findings": report.total_findings,
        "by_severity": report.by_severity,
        "by_category": report.by_category,
        "findings": [
            {
                "file": str(f.file),
                "line": f.line,
                "rule_id": f.rule_id,
                "severity": f.severity,
                "category": f.category,
                "message": f.message,
                "snippet": f.snippet,
            }
            for f in report.findings[:100]
        ],
    }


@router.get("/api/audit/outbound")
async def audit_outbound(
    since_days: int = Query(30, le=365),
    user: CurrentUser = Depends(get_current_user),
):
    """List outbound API call events from the audit log."""
    if not user.is_admin:
        raise HTTPException(status_code=403, detail="admin only")
    from agent_brain.memory.governance.audit.outbound import list_outbound_events

    events = list_outbound_events(since_days=since_days)
    return {
        "events": [e.to_dict() for e in events],
        "count": len(events),
        "since_days": since_days,
    }


__all__ = ["AuditScanRequest", "audit_outbound", "audit_scan", "get_audit_log", "router"]
