from fastapi.testclient import TestClient

from web.app import app


def _preflight(origin: str):
    return TestClient(app).options(
        "/api/health",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "GET",
            "Access-Control-Request-Headers": "authorization",
        },
    )


def test_cors_allows_loopback_web_admin_origin():
    resp = _preflight("http://127.0.0.1:8765")

    assert resp.status_code == 200
    assert resp.headers["access-control-allow-origin"] == "http://127.0.0.1:8765"
    assert resp.headers["access-control-allow-credentials"] == "true"


def test_cors_rejects_untrusted_origin_by_default():
    resp = _preflight("https://attacker.example")

    assert resp.status_code == 400
    assert "access-control-allow-origin" not in resp.headers


def test_cors_extra_origins_are_explicit_env_list(monkeypatch):
    import web.app as web_app

    monkeypatch.setenv(
        "MEMORY_HUB_CORS_ORIGINS",
        "https://admin.example.internal, http://192.0.2.2:8765, ",
    )

    assert web_app._cors_origins_from_env() == [
        "https://admin.example.internal",
        "http://192.0.2.2:8765",
    ]
