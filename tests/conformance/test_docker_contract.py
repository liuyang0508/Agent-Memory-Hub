from pathlib import Path


def test_default_docker_image_installs_web_runtime_and_uses_real_health_route() -> None:
    dockerfile = Path("deploy/Dockerfile").read_text(encoding="utf-8")
    compose = Path("deploy/docker-compose.yml").read_text(encoding="utf-8")

    assert 'pip install --no-cache-dir -e ".[web,embeddings]"' in dockerfile
    assert "|| pip install" not in dockerfile
    assert "COPY README.md LICENSE ./" in dockerfile
    assert "/api/health" in dockerfile
    assert "/api/health" in compose


def test_all_extra_is_defined_for_full_end_user_install() -> None:
    pyproject = Path("pyproject.toml").read_text(encoding="utf-8")

    assert "all = [" in pyproject


def test_docker_smoke_covers_auth_and_restart_persistence() -> None:
    smoke = Path("scripts/docker-smoke.sh").read_text(encoding="utf-8")

    assert "/api/auth/init" in smoke
    assert "/api/auth/me" in smoke
    assert "docker restart" in smoke
    assert "--mount source=" in smoke
    assert "MEMORY_HUB_NO_MODEL=1" in smoke
    assert "wait_for_login" in smoke
