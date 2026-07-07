from __future__ import annotations

import warnings
from pathlib import Path


def test_token_round_trip_does_not_emit_deprecation_warning(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setenv("BRAIN_DIR", str(tmp_path))

    from web.auth import create_token, decode_token

    token = create_token({"username": "admin", "tenant_id": "default", "role": "admin"})

    with warnings.catch_warnings():
        warnings.simplefilter("error", DeprecationWarning)
        payload = decode_token(token)

    assert payload["sub"] == "admin"
    assert payload["tenant_id"] == "default"
    assert payload["role"] == "admin"
