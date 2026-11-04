"""Client self-update: converge on the version the organization's Hub runs.

A team only benefits from a release once every member runs it, and nobody
remembers to reinstall. The resident worker therefore checks once a day and,
when the Hub reports a newer version than the one installed, installs exactly
that version:

* **The Hub sets the target.** The client never moves past its Hub, so "deploy
  the Hub first, then the clients" holds by construction, and a Hub that does
  not report its version (older than 1.4.14) is never followed.
* **Verified before it runs.** The wheel comes from the project's release
  page and must match the release's ``checksums.txt``; after installing, a
  fresh interpreter must import the new code and report the new version, or
  the previous version is put back.
* **Only a managed install updates itself.** A wheel installed by the
  installer into its own virtualenv updates; a source checkout, an editable
  install or a bundled runtime (Cowork) never does. One profile per machine
  (the one serving recall) updates, under a lock shared by every profile.
* **The member stays in control.** ``amh-native update --off`` (or
  ``AMH_AUTO_UPDATE=0``) turns it off; ``amh-native update`` shows the state.

Standard library only: the worker that calls this ships in portable runtimes.
"""

from __future__ import annotations

import hashlib
import importlib
import importlib.metadata
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib import request

PACKAGE = "agent-memory-hub"
RELEASE_BASE = "https://github.com/liuyang0508/agent-memory-hub/releases/download"
INTERVAL = 86400
RETRY_AFTER_ERROR = 3600
STATUS = "update.json"
LOCK = ".amh-update.lock"
MAX_BYTES = 50_000_000
_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)$")

Fetch = Callable[[str], bytes]
Run = Callable[[list[str]], int]


def parse(version: Any) -> tuple[int, int, int] | None:
    match = _VERSION.match(str(version or "").strip())
    return (int(match[1]), int(match[2]), int(match[3])) if match else None


def installed_version() -> str | None:
    """The version on disk now (not the one this process imported)."""
    importlib.invalidate_caches()
    try:
        return importlib.metadata.version(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return None


def managed_install() -> bool:
    """True for a wheel the installer put into this interpreter's own virtualenv."""
    if os.name != "posix" or sys.prefix == getattr(sys, "base_prefix", sys.prefix):
        return False
    try:
        dist = importlib.metadata.distribution(PACKAGE)
    except importlib.metadata.PackageNotFoundError:
        return False
    direct = dist.read_text("direct_url.json")
    if direct:
        try:
            if json.loads(direct).get("dir_info", {}).get("editable"):
                return False
        except ValueError:
            return False
    prefix = Path(sys.prefix).resolve()
    running = Path(__file__).resolve()
    installed = Path(str(dist.locate_file("agent_brain"))).resolve()
    return prefix in running.parents and prefix in installed.parents


def enabled(config: dict[str, Any]) -> bool:
    if os.environ.get("AMH_AUTO_UPDATE", "1").strip().lower() in ("0", "off", "false", "no"):
        return False
    return config.get("auto_update", True) is not False


def status(state: Path) -> dict[str, Any]:
    try:
        value = json.loads((state / STATUS).read_text())
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


def due(state: Path, *, now: float | None = None, interval: float = INTERVAL) -> bool:
    now = time.time() if now is None else now
    checked = status(state).get("checked")
    return not isinstance(checked, (int, float)) or now - float(checked) >= interval


def _fetch(url: str) -> bytes:
    with request.urlopen(url, timeout=60) as response:  # noqa: S310 - fixed https release URL
        data: bytes = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("release file too large")
    return data


def _run(argv: list[str]) -> int:
    return subprocess.run(argv, capture_output=True, timeout=600).returncode


def _wheel_name(version: str) -> str:
    return f"agent_memory_hub-{version}-py3-none-any.whl"


def _download(version: str, base: str, fetch: Fetch, into: Path) -> Path:
    """The release wheel, only if it matches the release's published checksum."""
    name = _wheel_name(version)
    sums = fetch(f"{base}/v{version}/checksums.txt").decode("utf8", errors="replace")
    expected = next(
        (line.split()[0].lower() for line in sums.splitlines() if line.strip().endswith(name)),
        None,
    )
    if not expected or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise ValueError("release has no checksum for the wheel")
    data = fetch(f"{base}/v{version}/{name}")
    if hashlib.sha256(data).hexdigest() != expected:
        raise ValueError("checksum mismatch")
    path = into / name
    path.write_bytes(data)
    return path


def _install(wheel: Path, run: Run) -> bool:
    argv = [sys.executable, "-m", "pip", "install", "--quiet", "--disable-pip-version-check"]
    return run([*argv, f"{wheel}[web]"]) == 0


def _verify(version: str, run: Run) -> bool:
    """A fresh interpreter imports the new runtime and reports the expected version."""
    probe = (
        "import importlib.metadata as m, agent_brain.native_sync.hooks, "
        "agent_brain.native_sync.worker; "
        f"raise SystemExit(0 if m.version({PACKAGE!r}) == {version!r} else 1)"
    )
    return run([sys.executable, "-I", "-c", probe]) == 0


def check(
    state: Path,
    config: dict[str, Any],
    transport: Any,
    *,
    now: float | None = None,
    fetch: Fetch = _fetch,
    run: Run = _run,
    managed: Callable[[], bool] = managed_install,
) -> dict[str, Any]:
    """One check; installs the Hub's version when it is newer. Never raises."""
    now = time.time() if now is None else now
    current = installed_version()
    report: dict[str, Any] = {"checked": now, "installed": current, "restart": False}
    try:
        report.update(_check(state, config, transport, current, fetch, run, managed))
    except Exception as exc:  # noqa: BLE001 - an update problem must never stop sync
        report.update({"result": "error", "error": type(exc).__name__})
        # An unreachable Hub or release page says nothing about tomorrow's check:
        # try again within the hour rather than waiting a whole day.
        report["checked"] = now - max(0.0, INTERVAL - RETRY_AFTER_ERROR)
    try:
        previous = status(state)
        history = list(previous.get("history") or [])[-9:]
        if report.get("result") in ("updated", "rolled_back"):
            history.append({k: report.get(k) for k in ("checked", "result", "from", "to")})
        tmp = state / (STATUS + ".tmp")
        tmp.write_text(json.dumps({**report, "history": history}, ensure_ascii=False))
        tmp.replace(state / STATUS)
    except OSError:
        pass
    return report


def _attempt(wheel: Path, version: str, run: Run) -> bool:
    """Install and verify. A pip timeout or a full disk is a failed attempt, not an
    exception: the caller still has to put the previous version back."""
    try:
        return _install(wheel, run) and _verify(version, run)
    except (OSError, subprocess.SubprocessError):
        return False


def _check(
    state: Path,
    config: dict[str, Any],
    transport: Any,
    current: str | None,
    fetch: Fetch,
    run: Run,
    managed: Callable[[], bool],
) -> dict[str, Any]:
    health = transport.health()
    target = health.get("version") if isinstance(health, dict) else None
    if parse(target) is None:
        return {"result": "hub_version_unknown"}
    report = {"hub": target}
    if parse(current) is not None and parse(target) <= parse(current):  # type: ignore[operator]
        return {**report, "result": "up_to_date"}
    if not managed():
        return {**report, "result": "not_managed"}
    import fcntl

    base = (os.environ.get("AMH_UPDATE_BASE") or config.get("update_base") or RELEASE_BASE).rstrip(
        "/"
    )
    with (Path(sys.prefix) / LOCK).open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return {**report, "result": "busy"}
        current = installed_version()  # another profile's worker may have just done it
        if parse(current) is not None and parse(target) <= parse(current):  # type: ignore[operator]
            return {**report, "result": "up_to_date", "restart": True}
        with tempfile.TemporaryDirectory(prefix="amh-update-") as work:
            wheel = _download(str(target), base, fetch, Path(work))
            if _attempt(wheel, str(target), run):
                return {
                    **report,
                    "result": "updated",
                    "from": current,
                    "to": target,
                    "restart": True,
                }
            # Put the previous version back; the worker keeps running it.
            restored = False
            if parse(current) is not None:
                try:
                    old = _download(str(current), base, fetch, Path(work))
                    restored = _attempt(old, str(current), run)
                except (OSError, ValueError):
                    restored = False
            return {
                **report,
                "result": "rolled_back" if restored else "error",
                "error": "install_or_verify_failed",
                "from": current,
                "to": target,
                "restart": restored,
            }
