"""Client self-update: follow the Hub's version, verified, reversible, never by surprise."""

import hashlib
import json

import pytest

from agent_brain.native_sync import autoupdate

BASE = "https://example.invalid/releases/download"


class Hub:
    def __init__(self, version):
        self.version = version

    def health(self):
        return {"protocol": 1, "version": self.version} if self.version else {"protocol": 1}


def release(*versions, corrupt=()):
    files = {}
    for v in versions:
        wheel = f"wheel-{v}".encode()
        name = autoupdate._wheel_name(v)
        digest = hashlib.sha256(wheel).hexdigest()
        files[f"{BASE}/v{v}/{name}"] = b"tampered" if v in corrupt else wheel
        files[f"{BASE}/v{v}/checksums.txt"] = f"{digest}  {name}\n".encode()

    def fetch(url):
        if url not in files:
            raise OSError("404")
        return files[url]

    return fetch


class Pip:
    """Records installs; `fail` versions install but do not verify."""

    def __init__(self, installed, fail=()):
        self.installed, self.fail, self.calls = installed, set(fail), []

    def __call__(self, argv):
        self.calls.append(argv)
        if "pip" in argv:
            self.installed = argv[-1].split("agent_memory_hub-")[1].split("-py3")[0]
            return 0
        wanted = argv[-1].split("== ")[1].split(" else")[0].strip("'")
        return 0 if wanted == self.installed and wanted not in self.fail else 1


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AMH_UPDATE_BASE", BASE)
    monkeypatch.setattr(autoupdate.sys, "prefix", str(tmp_path))
    state = tmp_path / "state"
    state.mkdir()
    return state


def run_check(state, monkeypatch, *, hub, installed, fetch, pip=None, managed=True):
    pip = pip or Pip(installed)
    monkeypatch.setattr(autoupdate, "installed_version", lambda: pip.installed)
    report = autoupdate.check(
        state, {}, Hub(hub), now=1000.0, fetch=fetch, run=pip, managed=lambda: managed
    )
    return report, pip


def test_updates_to_the_hubs_version_after_verifying_the_checksum(env, monkeypatch):
    report, pip = run_check(
        env, monkeypatch, hub="1.4.14", installed="1.4.13", fetch=release("1.4.14")
    )
    assert report["result"] == "updated" and report["restart"]
    assert (report["from"], report["to"]) == ("1.4.13", "1.4.14")
    assert pip.calls[0][-1].endswith("agent_memory_hub-1.4.14-py3-none-any.whl[web]")
    saved = json.loads((env / autoupdate.STATUS).read_text())
    assert saved["history"][-1]["to"] == "1.4.14" and saved["checked"] == 1000.0


def test_never_moves_past_or_without_the_hub(env, monkeypatch):
    for hub, expected in (
        ("1.4.13", "up_to_date"),
        ("1.4.12", "up_to_date"),
        (None, "hub_version_unknown"),
    ):
        report, pip = run_check(
            env, monkeypatch, hub=hub, installed="1.4.13", fetch=release("1.4.14")
        )
        assert report["result"] == expected and not pip.calls and not report["restart"]


def test_a_tampered_wheel_is_never_installed(env, monkeypatch):
    report, pip = run_check(
        env,
        monkeypatch,
        hub="1.4.14",
        installed="1.4.13",
        fetch=release("1.4.14", corrupt={"1.4.14"}),
    )
    assert report["result"] == "error" and report["error"] == "ValueError" and not pip.calls


def test_a_build_that_fails_to_verify_is_rolled_back(env, monkeypatch):
    pip = Pip("1.4.13", fail={"1.4.14"})
    report, pip = run_check(
        env,
        monkeypatch,
        hub="1.4.14",
        installed="1.4.13",
        fetch=release("1.4.13", "1.4.14"),
        pip=pip,
    )
    assert report["result"] == "rolled_back" and pip.installed == "1.4.13"


def test_source_checkouts_and_switched_off_clients_do_not_update(env, monkeypatch):
    report, pip = run_check(
        env, monkeypatch, hub="1.4.14", installed="1.4.13", fetch=release("1.4.14"), managed=False
    )
    assert report["result"] == "not_managed" and not pip.calls
    assert not autoupdate.enabled({"auto_update": False})
    monkeypatch.setenv("AMH_AUTO_UPDATE", "0")
    assert not autoupdate.enabled({})


def test_checks_at_most_daily(env):
    assert autoupdate.due(env, now=0.0)
    (env / autoupdate.STATUS).write_text(json.dumps({"checked": 1000.0}))
    assert not autoupdate.due(env, now=1000.0 + 3600)
    assert autoupdate.due(env, now=1000.0 + autoupdate.INTERVAL)


def test_this_test_environment_is_not_a_managed_install():
    # The repository's own editable install must never update itself.
    assert autoupdate.managed_install() is False


def test_health_reports_the_hub_version(tmp_path):
    from fastapi.testclient import TestClient

    from agent_brain._version import __version__
    from agent_brain.native_sync.api import create_app
    from agent_brain.native_sync.hub import Hub as RealHub

    body = TestClient(create_app(RealHub(tmp_path / "hub.sqlite"))).get("/native/v1/health").json()
    assert body["version"] == __version__


def test_a_failed_check_retries_within_the_hour_and_a_pip_timeout_still_restores(tmp_path):
    import subprocess

    from agent_brain.native_sync import autoupdate

    class Down:
        def health(self):
            raise OSError("Hub unreachable")

    report = autoupdate.check(tmp_path, {}, Down(), now=1_000_000.0)
    assert report["result"] == "error"
    assert not autoupdate.due(tmp_path, now=1_000_000.0 + 60)
    assert autoupdate.due(tmp_path, now=1_000_000.0 + autoupdate.RETRY_AFTER_ERROR + 1)

    def times_out(argv):
        raise subprocess.TimeoutExpired(argv, 600)

    assert autoupdate._attempt(tmp_path / "x.whl", "9.9.9", times_out) is False
