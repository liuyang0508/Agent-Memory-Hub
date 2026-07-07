from __future__ import annotations


def test_web_auth_storage_helpers_are_split_and_auth_wrappers_preserve_api() -> None:
    from web import auth
    from web import auth_storage

    assert auth_storage.load_users
    assert auth_storage.save_users
    assert auth_storage.secret_key
    assert auth._load_users
    assert auth._save_users
    assert auth._secret_key
