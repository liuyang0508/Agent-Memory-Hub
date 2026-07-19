from __future__ import annotations

import json
from pathlib import Path


def test_public_hygiene_scanner_catches_local_and_internal_literals() -> None:
    from agent_brain.evaluation.public_hygiene import scan_text

    user = "sarah" + "chen"
    domain = "gitlab." + "acme-corp." + "dev"
    rank = "618" + "234"
    findings = scan_text(
        f"repo=/Users/{user}/Desktop/example-project\n"
        f"mirror={domain}/team/repo\n"
        f"candidate rank={rank}\n",
        path="fixture.txt",
        identity_terms=(user,),
    )

    assert {finding.rule for finding in findings} == {
        "personal_absolute_path",
        "personal_handle",
        "private_code_host_domain",
        "contextual_rank",
    }


def test_public_hygiene_scanner_uses_categories_not_fixed_literals() -> None:
    from agent_brain.evaluation.public_hygiene import scan_text

    user = "morgan" + "wu"
    domain = "code." + "orion-platform." + "dev"
    rank = "734" + "567"
    secret = "sk_live_" + "abcdefghijklmnopqrstuvwxyz123456"
    findings = scan_text(
        f"encoded=Users-{user}--Desktop--repo\n"
        f"remote={domain}/scm/repo\n"
        f"score=612 rank={rank}\n"
        f"api_key='{secret}'\n",
        path="fixture.txt",
        identity_terms=(user,),
    )

    assert {finding.rule for finding in findings} == {
        "encoded_personal_path",
        "personal_handle",
        "private_code_host_domain",
        "score_rank_pair",
        "secret_assignment",
    }


def test_public_hygiene_scanner_uses_risk_features_beyond_named_examples() -> None:
    from agent_brain.evaluation.public_hygiene import scan_text

    jwt = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiJvcmdfMTIzNDU2Nzg5In0." + "signaturepart"
    uuid = "018f47d2" + "-1d7c-7a98-8b1d-" + "28a25d6e4c31"
    secretish = "p9V4mN2xQ7rS8tU3wY6zA1bC5dE0fG"
    ip = "10.24" + ".5.6"
    uri = "https://" + "svc:realpassw0rd" + "@" + ip + ":8443/api"
    email = "release.operator" + "@" + "orion-platform.dev"
    findings = scan_text(
        f"endpoint={uri}\n"
        f"Authorization: Bearer {jwt}\n"
        f"owner={email}\n"
        f"tenant_id={uuid}\n"
        f"session_material='{secretish}'\n",
        path="fixture.txt",
    )

    assert {finding.rule for finding in findings} >= {
        "credentialed_uri",
        "private_network_address",
        "auth_header_secret",
        "jwt_literal",
        "email_address",
        "contextual_identifier",
        "high_entropy_secret",
    }


def test_public_hygiene_scanner_distinguishes_ssh_authority_from_email() -> None:
    from agent_brain.evaluation.public_hygiene import redact_public_text, scan_text

    ssh_authority = "git" + "@" + "gitee.com"
    email = "release.operator" + "@" + "orion-platform.dev"
    findings = scan_text(
        f"remote={ssh_authority}:team/example.git\n"
        f"git remote add mirror {ssh_authority}:repo\n"
        f"owner={email}\n"
        f"owner={email}:docs/private\n"
        f"git clone {ssh_authority}:repo owner={email}:docs/private\n",
        path="fixture.txt",
    )

    assert [
        (finding.line, finding.rule, finding.match)
        for finding in findings
    ] == [
        (3, "email_address", email),
        (4, "email_address", email),
        (5, "email_address", email),
    ]
    assert redact_public_text(
        f"remote={ssh_authority}:team/example.git owner={email}:docs/private"
    ) == (
        f"remote={ssh_authority}:team/example.git "
        "owner=user@example.com:docs/private"
    )
    assert redact_public_text(
        f"git clone {ssh_authority}:repo owner={email}:docs/private"
    ) == (
        f"git clone {ssh_authority}:repo "
        "owner=user@example.com:docs/private"
    )


def test_public_hygiene_scanner_allows_documentation_ips_but_flags_private_ips() -> None:
    from agent_brain.evaluation.public_hygiene import scan_text

    documentation_ip = "192.0" + ".2.2"
    private_ip = "10.24" + ".5.6"
    findings = scan_text(
        f"example=http://{documentation_ip}:8765\n"
        f"internal=http://{private_ip}:8765\n",
        path="fixture.txt",
    )

    assert [
        (finding.line, finding.rule, finding.match)
        for finding in findings
    ] == [(2, "private_network_address", private_ip)]


def test_public_hygiene_redacts_report_text() -> None:
    from agent_brain.evaluation.public_hygiene import public_path, redact_public_text

    root = Path("/tmp/example-repo")
    user = "sarah" + "chen"
    domain = "code." + "acme-corp." + "dev"
    rank = "618" + "234"
    text = (
        "/tmp/example-repo/docs/evaluation/report.json "
        f"/Users/{user}/.codex/config.toml "
        f"{domain}/project "
        f"rank={rank}"
    )

    assert redact_public_text(text, root=root) == (
        "<repo>/docs/evaluation/report.json "
        "~/.codex/config.toml "
        "example.internal/project "
        "rank=<rank>"
    )
    assert public_path(root / "docs/evaluation/report.json", root=root) == "docs/evaluation/report.json"


def test_public_hygiene_redacts_feature_based_risks() -> None:
    from agent_brain.evaluation.public_hygiene import redact_public_text

    jwt = "eyJhbGciOiJIUzI1NiJ9." + "eyJzdWIiOiJvcmdfMTIzNDU2Nzg5In0." + "signaturepart"
    uuid = "018f47d2" + "-1d7c-7a98-8b1d-" + "28a25d6e4c31"
    secretish = "p9V4mN2xQ7rS8tU3wY6zA1bC5dE0fG"
    ip = "10.24" + ".5.6"
    uri = "https://" + "svc:realpassw0rd" + "@" + ip + ":8443/api"
    email = "release.operator" + "@" + "orion-platform.dev"

    redacted = redact_public_text(
        f"endpoint={uri}\n"
        f"Authorization: Bearer {jwt}\n"
        f"owner={email}\n"
        f"tenant_id={uuid}\n"
        f"session_material='{secretish}'\n"
    )

    assert redacted == (
        "endpoint=https://<user>:<secret>@<private-ip>:8443/api\n"
        "Authorization: Bearer <secret>\n"
        "owner=user@example.com\n"
        "tenant_id=<id>\n"
        "session_material='<secret>'\n"
    )


def test_public_hygiene_preserves_json_when_email_follows_backslash_escape() -> None:
    from agent_brain.evaluation.public_hygiene import redact_public_text

    email = "release.operator" + "@" + "orion-platform.dev"
    source = rf'{{"context":"latex=\{email}"}}'
    redacted = redact_public_text(source)

    assert json.loads(redacted)["context"] == r"latex=\user@example.com"

    home_path = "/" + "home" + "/" + "alpt"
    path_source = json.dumps({"context": f'node = tree.get_node("{home_path}")'})
    path_redacted = redact_public_text(path_source)

    assert json.loads(path_redacted)["context"] == 'node = tree.get_node("~")'


def test_public_hygiene_allows_public_code_host_and_reserved_invalid_email() -> None:
    from agent_brain.evaluation.public_hygiene import redact_public_text, scan_text

    sensitive_email = "release-owner" + "@" + "corp.private"
    findings = scan_text(
        "git remote add mirror git@gitee.com:team/repo.git\n"
        "git -c user.email=amh-test@example.invalid commit\n"
        f"{sensitive_email}:team/repo.git\n",
        path="release-example.md",
    )

    assert [(finding.rule, finding.match) for finding in findings] == [
        ("email_address", sensitive_email)
    ]
    assert redact_public_text(f"owner={sensitive_email}:team/repo.git") == (
        "owner=user@example.com:team/repo.git"
    )


def test_git_tracked_public_surface_has_no_sensitive_literals() -> None:
    from agent_brain.evaluation.public_hygiene import format_findings, scan_git_public_surface

    root = Path(__file__).resolve().parents[2]
    findings = scan_git_public_surface(root)

    assert findings == [], format_findings(findings)
