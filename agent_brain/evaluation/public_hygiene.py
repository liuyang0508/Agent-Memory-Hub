"""Public-release hygiene checks and redaction helpers."""

from __future__ import annotations

import argparse
import base64
import ipaddress
import math
import os
import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence
from urllib.parse import SplitResult, urlsplit, urlunsplit


@dataclass(frozen=True)
class PublicHygieneFinding:
    path: str
    line: int
    rule: str
    match: str


_RULES: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "personal_absolute_path",
        re.compile(
            r"/(?:Users|home)/"
            r"(?!(?:\.\.\.|example|yourname|your-user|user|runner|memhub|tmp|path|workspace|repo|project|test-user|alice|bob)(?:/|$))"
            r"[A-Za-z0-9._-]+(?:/[^\s`\"'<>)]*)?"
        ),
    ),
    (
        "encoded_personal_path",
        re.compile(
            r"\bUsers-(?!example\b|your-user\b|test-user\b|user\b|runner\b)"
            r"[A-Za-z0-9._-]{3,}(?=\b|--|-)"
        ),
    ),
    ("aws_access_key_literal", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("private_key_marker", re.compile(r"BEGIN (?:RSA |OPENSSH |EC |DSA )?PRIVATE KEY")),
    (
        "secret_assignment",
        re.compile(
            r"(?i)\b(?:api[_-]?key|access[_-]?key|secret|token|password|credential)"
            r"\b\s*[:=]\s*['\"]"
            r"(?!(?:<|example|placeholder|redacted|dummy|test|xxx))"
            r"[A-Za-z0-9_./+=:-]{12,}"
        ),
    ),
)

_DOMAIN_RE = re.compile(r"\b(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
_URI_RE = re.compile(r"\b[A-Za-z][A-Za-z0-9+.-]*://[^\s`\"'<>)]*")
_EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@(?:[A-Za-z0-9-]+\.)+[A-Za-z]{2,}\b")
_IPV4_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_UUID_RE = re.compile(
    r"\b[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}\b"
)
_JWT_RE = re.compile(r"\b[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{8,}\b")
_AUTH_HEADER_RE = re.compile(
    r"(?i)\b(?:authorization|proxy-authorization|cookie|set-cookie)\s*:\s*"
    r"(?:(?:bearer|basic)\s+)?([A-Za-z0-9._~+/=-]{12,})"
)
_ASSIGNMENT_RE = re.compile(
    r"(?P<key>[A-Za-z_][A-Za-z0-9_.-]{1,80})"
    r"\s*(?<!:)[:=](?!:)\s*"
    r"(?P<quote>['\"]?)"
    r"(?P<value>[^'\"\s#]+)"
    r"(?P=quote)"
)
_PUBLIC_DOMAIN_ALLOWLIST = {
    "example.com",
    "example.net",
    "example.org",
    "example.test",
    "example.internal",
    "example.invalid",
    "github.com",
    "gitee.com",
    "gitlab.com",
    "bitbucket.org",
    "codeberg.org",
}
_DOMAIN_TLDS = {
    "ai",
    "app",
    "cn",
    "com",
    "dev",
    "io",
    "net",
    "org",
}
_PRIVATE_DOMAIN_LABELS = {
    "corp",
    "internal",
    "intranet",
    "lan",
    "private",
}
_CODE_HOST_LABELS = {
    "code",
    "gerrit",
    "ghe",
    "gitlab",
    "scm",
    "stash",
}
_RANK_LABEL_RE = re.compile(
    r"(?i)(?:\b(?:rank|ranking|position)\b|位次|排名)\s*[:=：]?\s*\d{4,9}"
)
_SCORE_RANK_PAIR_RE = re.compile(
    r"(?i)(?:\bscore\b|分数|成绩)?[^。\n]{0,32}"
    r"(?:[3-7]\d{2}\s*分?)[^。\n]{0,32}"
    r"(?:\b(?:rank|ranking|position)\b|位次|排名)\s*[:=：]?\s*\d{4,9}"
)
_SAFE_IDENTITY_TERMS = {
    "admin",
    "agent",
    "alice",
    "bob",
    "codex",
    "example",
    "guest",
    "memhub",
    "project",
    "repo",
    "root",
    "runner",
    "test",
    "user",
    "workspace",
}
_IDENTIFIER_CONTEXT_RE = re.compile(
    r"(?i)(?:account|conversation|customer|device|installation|org|profile|"
    r"request|session|tenant|trace|user|workspace)[_.-]?(?:id|uuid)?"
)
_PLACEHOLDER_VALUES = {
    "<secret>",
    "<token>",
    "changeme",
    "dummy",
    "example",
    "placeholder",
    "redacted",
    "test",
    "xxx",
}
_DOCUMENTATION_IPV4_NETWORKS = tuple(
    ipaddress.ip_network(cidr)
    for cidr in (
        "192.0.2.0/24",
        "198.51.100.0/24",
        "203.0.113.0/24",
    )
)

_DEFAULT_SKIP_PREFIXES = (
    ".git/",
    ".cache/",
    ".mypy_cache/",
    ".pytest_cache/",
    ".ruff_cache/",
    ".venv/",
    "__pycache__/",
    "build/",
    "dist/",
    "tests/fixtures/malicious_skills/",
)


def redact_public_text(text: str, *, root: Path | None = None) -> str:
    """Redact local-only values before persisting public reports."""

    effective_root = (root or Path.cwd()).expanduser().resolve()
    replacements = {
        str(root) if root is not None else str(effective_root): "<repo>",
        str(effective_root): "<repo>",
        str(Path.home()): "~",
    }
    redacted = text
    for raw, replacement in sorted(replacements.items(), key=lambda item: len(item[0]), reverse=True):
        if raw:
            redacted = redacted.replace(raw, replacement)
    redacted = re.sub(r"/Users/[^/\\\s`\"'<>)]*", "~", redacted)
    redacted = re.sub(r"/home/(?!memhub(?:/|$))[^/\\\s`\"'<>)]*", "~", redacted)
    redacted = _URI_RE.sub(lambda match: _redact_uri(match.group(0)), redacted)
    redacted = _AUTH_HEADER_RE.sub(lambda match: match.group(0).replace(match.group(1), "<secret>"), redacted)
    redacted = _JWT_RE.sub(lambda match: "<jwt>" if _looks_like_jwt(match.group(0)) else match.group(0), redacted)
    redacted = _EMAIL_RE.sub(_redact_email_match, redacted)
    redacted = _IPV4_RE.sub(
        lambda match: "<private-ip>" if _is_sensitive_ip_literal(match.group(0), line=redacted) else match.group(0),
        redacted,
    )
    redacted = _DOMAIN_RE.sub(
        lambda match: "example.internal" if _is_private_or_internal_domain(match.group(0)) else match.group(0),
        redacted,
    )
    redacted = _RANK_LABEL_RE.sub(lambda match: re.sub(r"\d{4,9}", "<rank>", match.group(0)), redacted)
    redacted = _SCORE_RANK_PAIR_RE.sub(
        lambda match: re.sub(r"\d{4,9}", "<rank>", match.group(0)),
        redacted,
    )
    redacted = "".join(_redact_feature_risks_in_line(line) for line in redacted.splitlines(keepends=True))
    return redacted


def public_path(path: str | Path, *, root: Path | None = None) -> str:
    """Return a repo-relative path when possible, otherwise a redacted path."""

    candidate = Path(path).expanduser()
    effective_root = (root or Path.cwd()).expanduser().resolve()
    try:
        resolved = candidate.resolve()
    except OSError:
        resolved = candidate
    try:
        return resolved.relative_to(effective_root).as_posix()
    except ValueError:
        return redact_public_text(str(resolved), root=effective_root)


def scan_text(
    text: str,
    *,
    path: str = "<memory>",
    identity_terms: Sequence[str] | None = None,
) -> list[PublicHygieneFinding]:
    findings: list[PublicHygieneFinding] = []
    identities = tuple(_identity_terms(identity_terms))
    for line_no, line in enumerate(text.splitlines(), start=1):
        for rule, pattern in _RULES:
            for match in pattern.finditer(line):
                findings.append(
                    PublicHygieneFinding(
                        path=path,
                        line=line_no,
                        rule=rule,
                        match=_trim_match(match.group(0)),
                    )
                )
        for rule, match_text in _dynamic_findings(line, identities=identities):
            findings.append(
                PublicHygieneFinding(
                    path=path,
                    line=line_no,
                    rule=rule,
                    match=_trim_match(match_text),
                )
            )
    return findings


def scan_paths(paths: Iterable[Path], *, root: Path | None = None) -> list[PublicHygieneFinding]:
    effective_root = (root or Path.cwd()).expanduser().resolve()
    findings: list[PublicHygieneFinding] = []
    for path in paths:
        if _should_skip(path, root=effective_root):
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\0" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        try:
            rel = path.resolve().relative_to(effective_root).as_posix()
        except ValueError:
            rel = str(path)
        findings.extend(scan_text(text, path=rel))
    return findings


def git_public_files(root: Path, *, include_untracked: bool = False) -> list[Path]:
    args = ["git", "ls-files", "-z"]
    if include_untracked:
        args.extend(["--others", "--exclude-standard"])
    result = subprocess.run(args, cwd=root, check=True, capture_output=True)
    return [
        root / item.decode("utf-8")
        for item in result.stdout.split(b"\0")
        if item
    ]


def scan_git_public_surface(root: Path, *, include_untracked: bool = False) -> list[PublicHygieneFinding]:
    files = git_public_files(root, include_untracked=include_untracked)
    return scan_paths(files, root=root)


def format_findings(findings: Sequence[PublicHygieneFinding]) -> str:
    return "\n".join(
        f"{finding.path}:{finding.line}: {finding.rule}: {finding.match}"
        for finding in findings
    )


def _should_skip(path: Path, *, root: Path) -> bool:
    try:
        rel = path.resolve().relative_to(root).as_posix()
    except ValueError:
        rel = path.as_posix()
    return any(rel == prefix.rstrip("/") or rel.startswith(prefix) for prefix in _DEFAULT_SKIP_PREFIXES)


def _trim_match(value: str, *, limit: int = 120) -> str:
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _dynamic_findings(line: str, *, identities: Sequence[str]) -> Iterable[tuple[str, str]]:
    for identity in identities:
        pattern = re.compile(r"(?<![A-Za-z0-9_])" + re.escape(identity) + r"(?![A-Za-z0-9_])", re.IGNORECASE)
        for match in pattern.finditer(line):
            yield "personal_handle", match.group(0)

    for match in _DOMAIN_RE.finditer(line):
        domain = match.group(0)
        if _is_private_or_internal_domain(domain):
            yield "private_code_host_domain", domain

    for match in _URI_RE.finditer(line):
        uri = match.group(0)
        parsed = _safe_urlsplit(uri)
        if parsed is not None and _uri_has_concrete_credentials(parsed):
            yield "credentialed_uri", uri

    for match in _IPV4_RE.finditer(line):
        ip_literal = match.group(0)
        if _is_sensitive_ip_literal(ip_literal, line=line):
            yield "private_network_address", ip_literal

    for match in _AUTH_HEADER_RE.finditer(line):
        value = match.group(1)
        if not _looks_like_placeholder(value):
            yield "auth_header_secret", value

    for match in _JWT_RE.finditer(line):
        jwt = match.group(0)
        if _looks_like_jwt(jwt):
            yield "jwt_literal", jwt

    for match in _EMAIL_RE.finditer(line):
        email = match.group(0)
        if _is_sensitive_email(email) and not _looks_like_scp_ssh_remote(match):
            yield "email_address", email

    for match in _UUID_RE.finditer(line):
        uuid = match.group(0)
        if _line_has_identifier_context(line):
            yield "contextual_identifier", uuid

    for match in _ASSIGNMENT_RE.finditer(line):
        key = match.group("key")
        value = match.group("value")
        if _is_high_entropy_secret_assignment(key, value):
            yield "high_entropy_secret", value

    score_rank_spans: list[tuple[int, int]] = []
    for match in _SCORE_RANK_PAIR_RE.finditer(line):
        score_rank_spans.append(match.span())
        yield "score_rank_pair", match.group(0)

    for match in _RANK_LABEL_RE.finditer(line):
        if any(start <= match.start() and match.end() <= end for start, end in score_rank_spans):
            continue
        yield "contextual_rank", match.group(0)


def _identity_terms(explicit_terms: Sequence[str] | None = None) -> list[str]:
    raw_terms = [
        *(explicit_terms or ()),
        Path.home().name,
        os.environ.get("USER", ""),
        os.environ.get("LOGNAME", ""),
        os.environ.get("SUDO_USER", ""),
    ]
    terms: list[str] = []
    for raw in raw_terms:
        term = str(raw).strip().lower()
        if not _is_sensitive_identity_term(term):
            continue
        if term not in terms:
            terms.append(term)
    return terms


def _is_sensitive_identity_term(term: str) -> bool:
    if not term or term in _SAFE_IDENTITY_TERMS:
        return False
    if len(term) < 3:
        return False
    return bool(re.fullmatch(r"[a-z][a-z0-9._-]{2,31}", term))


def _is_private_or_internal_domain(domain: str) -> bool:
    lowered = domain.lower().strip(".")
    if _is_allowed_public_domain(lowered):
        return False
    labels = lowered.split(".")
    if not labels or labels[-1] not in _DOMAIN_TLDS:
        return False
    has_private_label = any(label in _PRIVATE_DOMAIN_LABELS for label in labels)
    has_private_suffix = any(label.endswith(("-inc", "-corp", "-internal")) for label in labels)
    if has_private_label:
        return True
    if has_private_suffix:
        return True
    if labels and labels[0] in _CODE_HOST_LABELS and labels[-1] not in {"com", "net", "org"}:
        return True
    return False


def _is_allowed_public_domain(domain: str) -> bool:
    if domain in _PUBLIC_DOMAIN_ALLOWLIST:
        return True
    return any(domain.endswith("." + allowed) for allowed in _PUBLIC_DOMAIN_ALLOWLIST)


def _safe_urlsplit(uri: str) -> SplitResult | None:
    try:
        return urlsplit(uri)
    except ValueError:
        return None


def _uri_has_concrete_credentials(parsed: SplitResult) -> bool:
    if parsed.username is None and parsed.password is None:
        return False
    credential_parts = [part for part in (parsed.username, parsed.password) if part]
    return any(not _looks_like_placeholder(part) for part in credential_parts)


def _redact_uri(uri: str) -> str:
    parsed = _safe_urlsplit(uri)
    if parsed is None:
        return uri
    netloc = parsed.netloc
    if _uri_has_concrete_credentials(parsed):
        host = parsed.hostname or "host"
        port = f":{parsed.port}" if parsed.port is not None else ""
        netloc = f"<user>:<secret>@{host}{port}"
    if parsed.hostname and _is_sensitive_ip_literal(parsed.hostname, line=uri):
        netloc = netloc.replace(parsed.hostname, "<private-ip>")
    return urlunsplit((parsed.scheme, netloc, parsed.path, parsed.query, parsed.fragment))


def _is_sensitive_ip_literal(value: str, *, line: str) -> bool:
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return False
    if not ip.version == 4:
        return False
    if ip.is_loopback or ip.is_link_local or ip.is_unspecified:
        return False
    if any(ip in network for network in _DOCUMENTATION_IPV4_NETWORKS):
        return False
    if _looks_like_network_range_example(value, line=line):
        return False
    return ip.is_private


def _looks_like_network_range_example(value: str, *, line: str) -> bool:
    return bool(re.search(rf"\b{re.escape(value)}\s*/\s*\d{{1,2}}\b", line))


def _is_sensitive_email(email: str) -> bool:
    domain = email.rsplit("@", 1)[-1].lower()
    if _is_allowed_public_domain(domain):
        return False
    if domain.endswith(".local") or domain == "localhost":
        return False
    return not any(part in email.lower() for part in ("noreply@", "no-reply@"))


def _redact_email_match(match: re.Match[str]) -> str:
    email = match.group(0)
    if not _is_sensitive_email(email) or _looks_like_scp_ssh_remote(match):
        return email
    replacement = "user@example.com"
    if _has_odd_backslash_prefix(match.string, match.start()):
        return "\\" + replacement
    return replacement


def _looks_like_scp_ssh_remote(match: re.Match[str]) -> bool:
    suffix = match.string[match.end():]
    path_match = re.match(r":(?P<path>[^\s`\"'<>]+)", suffix)
    if path_match is None:
        return False
    path = path_match.group("path").rstrip(".,;)")
    if not path:
        return False

    prefix = match.string[:match.start()].rstrip().rstrip("`\"'").rstrip()
    return bool(
        re.search(
            r"(?i)(?:"
            r"(?:^|\s)git\s+(?:clone|fetch|pull|push)"
            r"(?:\s+--?[^\s]+)*|"
            r"(?:^|\s)git\s+remote\s+(?:add|set-url)"
            r"(?:\s+--?[^\s]+)*\s+[^\s]+|"
            r"\bremote(?:\s+path)?(?:\s+as)?\s*(?:=|:)?\s*"
            r")$",
            prefix,
        )
    )


def _has_odd_backslash_prefix(text: str, index: int) -> bool:
    count = 0
    cursor = index - 1
    while cursor >= 0 and text[cursor] == "\\":
        count += 1
        cursor -= 1
    return count % 2 == 1


def _line_has_identifier_context(line: str) -> bool:
    return bool(_IDENTIFIER_CONTEXT_RE.search(line))


def _redact_feature_risks_in_line(line: str) -> str:
    redacted = line
    if _line_has_identifier_context(redacted):
        redacted = _UUID_RE.sub("<id>", redacted)

    def replace_assignment(match: re.Match[str]) -> str:
        key = match.group("key")
        value = match.group("value")
        if not _is_high_entropy_secret_assignment(key, value):
            return match.group(0)
        return match.group(0).replace(value, "<secret>", 1)

    return _ASSIGNMENT_RE.sub(replace_assignment, redacted)


def _is_high_entropy_secret_assignment(key: str, value: str) -> bool:
    if not _has_sensitive_assignment_context(key):
        return False
    if _looks_like_placeholder(value):
        return False
    if _looks_like_uri(value) or _looks_like_jwt(value):
        return False
    normalized = value.strip("'\"")
    if len(normalized) < 20:
        return False
    if (
        _UUID_RE.fullmatch(normalized)
        or _looks_like_slug(normalized)
        or _looks_like_dotted_identifier(normalized)
        or _looks_like_path_literal(normalized)
    ):
        return False
    if not re.fullmatch(r"[A-Za-z0-9_./+=:-]+", normalized):
        return False
    return _shannon_entropy(normalized) >= 3.5


def _has_sensitive_assignment_context(key: str) -> bool:
    segments = re.findall(r"[a-z0-9]+", key.lower())
    sensitive_segments = {
        "auth",
        "bearer",
        "client",
        "connection",
        "cookie",
        "credential",
        "credentials",
        "dsn",
        "password",
        "passwd",
        "pwd",
        "secret",
        "session",
        "token",
        "uri",
        "url",
    }
    if any(segment in sensitive_segments for segment in segments):
        return True
    return bool(
        re.search(r"(?i)(?:^|[_\-.])(?:api|access|private)?key(?:$|[_\-.])", key)
        or re.search(r"(?i)(?:api|access|private)key", key)
    )


def _looks_like_slug(value: str) -> bool:
    return bool(re.fullmatch(r"[a-z0-9]+(?:[-_][a-z0-9]+)+", value))


def _looks_like_dotted_identifier(value: str) -> bool:
    return bool(re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+", value))


def _looks_like_path_literal(value: str) -> bool:
    return value.startswith(("/", "./", "../", "~/")) or bool(
        re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_./-]+", value)
    )


def _looks_like_uri(value: str) -> bool:
    return _safe_urlsplit(value) is not None and "://" in value


def _looks_like_placeholder(value: str) -> bool:
    lowered = value.strip("'\"").lower()
    if lowered in _PLACEHOLDER_VALUES:
        return True
    if lowered.startswith(("<", "${", "$", "{{")):
        return True
    return any(marker in lowered for marker in ("example", "placeholder", "redacted", "dummy", "changeme", "your-"))


def _looks_like_jwt(value: str) -> bool:
    if not _JWT_RE.fullmatch(value):
        return False
    header = value.split(".", 1)[0]
    try:
        padded = header + "=" * (-len(header) % 4)
        decoded = base64.urlsafe_b64decode(padded.encode("ascii")).decode("utf-8", errors="ignore")
    except Exception:
        return False
    return '"alg"' in decoded or "'alg'" in decoded


def _shannon_entropy(value: str) -> float:
    if not value:
        return 0.0
    counts = {char: value.count(char) for char in set(value)}
    length = len(value)
    return -sum((count / length) * math.log2(count / length) for count in counts.values())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan public files for local-only or sensitive literals.")
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--include-untracked", action="store_true")
    args = parser.parse_args(argv)
    findings = scan_git_public_surface(args.root, include_untracked=args.include_untracked)
    if findings:
        print(format_findings(findings))
        return 1
    print("public hygiene: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PublicHygieneFinding",
    "format_findings",
    "git_public_files",
    "public_path",
    "redact_public_text",
    "scan_git_public_surface",
    "scan_paths",
    "scan_text",
]
