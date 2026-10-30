"""Cowork runtime boundary: pinned pairing, native path discovery and Hook dispatch.

Credentials live only in private runtime state. A distributable plugin contains
public Hub/member pins; its short-lived invitation is a separate local file.
CLAUDE_COWORK_MEMORY_PATH_OVERRIDE comes from the installed Cowork runtime, not
from a guessed host directory or instructions in a conversation.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from .client import NativeClient
from .common import atomic_json, digest, runtime_argv
from .storage import DB_TIMEOUT
from .runtime_sources import queue_observation
from .transport import Transport, validate_url

PAIRING_FILENAME = ".amh-native-connection.json"


def read_object(path: Path) -> dict[str, Any]:
    """Read bounded connection metadata without following a substituted file symlink."""
    if path.is_symlink():
        raise ValueError("runtime connection must not be a symlink")
    with path.open("rb") as stream:
        data = stream.read(16385)
    if len(data) > 16384:
        raise ValueError("runtime connection is too large")
    value = json.loads(data)
    if not isinstance(value, dict):
        raise ValueError("runtime connection must be an object")
    return value


def export_connection(state: Path, destination: Path) -> dict[str, str]:
    """Issue a one-use runtime ticket; return public pins suitable for a plugin package."""
    client = NativeClient(state)
    if destination.is_symlink():
        raise ValueError("connection destination must not be a symlink")
    transport = Transport(client.config["hub"], client.identity["token"])
    ticket = transport.call("runtime-invites", {})
    policy = {
        "hub": transport.url,
        "member_id": client.identity["member_id"],
        "tenant_id": client.identity["tenant_id"],
    }
    if any(ticket.get(k) != policy[k] for k in ("member_id", "tenant_id")):
        raise PermissionError("runtime ticket does not match this member")
    atomic_json(destination, {**ticket, "hub": transport.url, "protocol": 1})
    return policy


def validate_profile(client: NativeClient, policy: dict[str, Any]) -> None:
    """Never silently repoint an existing runtime's outbox to a new Hub/member."""
    validate_config(client.config, policy)


def validate_config(config: dict[str, Any], policy: dict[str, Any]) -> None:
    """Check public pins without opening the database on the Hook path."""
    if not policy:
        return  # Explicit pre-enrolled --state remains supported for local operators.
    if validate_url(config["hub"]) != validate_url(policy["hub"]) or any(
        config["identity"].get(k) != policy.get(k) for k in ("member_id", "tenant_id")
    ):
        raise PermissionError("runtime profile differs from the pinned organization/member")


def enroll_runtime(state: Path, policy: dict[str, Any], connection: Path) -> NativeClient:
    """Consume a scoped ticket once, validating pins before persisting any credential."""
    import fcntl

    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    with (state / "enrollment.lock").open("a") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if (state / "config.json").exists():
            client = NativeClient(state)
            validate_profile(client, policy)
            return client
        ticket = read_object(connection)
        if (
            not policy
            or ticket.get("protocol") != 1
            or any(
                not policy.get(k) or ticket.get(k) != policy[k]
                for k in ("hub", "member_id", "tenant_id")
            )
        ):
            raise PermissionError("connection file differs from the pinned organization/member")
        client = NativeClient.join(
            state,
            policy["hub"],
            ticket["invite"],
            "Claude Cowork runtime",
            expected_identity={k: policy[k] for k in ("member_id", "tenant_id")},
            timeout=5,
        )
        atomic_json(state / "runtime-status.json", {"phase": "enrolled", "time": time.time()})
        return client


def schedule_enrollment(state: Path, policy_path: Path, connection: Path) -> None:
    """Launch enrollment off the Hook path, coalescing retries for the same ticket."""
    import fcntl

    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if not connection.is_file():
        atomic_json(state / "runtime-status.json", {"phase": "connection-file-required"})
        return
    ticket_hash = digest(json.dumps(read_object(connection), sort_keys=True))
    status = state / "runtime-status.json"
    with (state / "enrollment-launch.lock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return
        previous = read_object(status) if status.exists() else {}
        if (
            previous.get("ticket_hash") == ticket_hash
            and time.time() - previous.get("time", 0) < 60
        ):
            return
        atomic_json(
            status, {"phase": "connecting", "ticket_hash": ticket_hash, "time": time.time()}
        )
        subprocess.Popen(
            runtime_argv(
                __name__,
                "--state",
                str(state),
                "--policy",
                str(policy_path),
                "--enroll",
                str(connection),
            ),
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
        )


def main() -> None:
    """Portable plugin entry; never print tickets or fail the product's normal task."""
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", type=Path)
    parser.add_argument("--policy", type=Path)
    parser.add_argument("--product", choices=["cowork"], default="cowork")
    parser.add_argument("--start-worker", action="store_true")
    parser.add_argument("--enroll", type=Path)
    parser.add_argument("--diagnose", action="store_true")
    args = parser.parse_args()
    state: Path | None = None
    timeout_context = DB_TIMEOUT.set(10 if args.enroll else 0.04)
    try:
        explicit = args.state or os.environ.get("AMH_NATIVE_HOME")
        data_root = os.environ.get("CLAUDE_PLUGIN_DATA")
        if args.diagnose:
            policy = read_object(args.policy) if args.policy else {}
            native = os.environ.get("CLAUDE_COWORK_MEMORY_PATH_OVERRIDE")
            diagnostic: dict[str, Any] = {
                "python": sys.version.split()[0],
                "persistent_state_supplied": bool(explicit or data_root),
                "native_memory_directory": native,
                "native_memory_directory_exists": bool(native and Path(native).is_dir()),
                "stable_project_id_present": bool(os.environ.get("CLAUDE_PROJECT_UUID")),
                "hub_reachable": False,
            }
            if policy.get("hub"):
                try:
                    health = Transport(policy["hub"], timeout=2).call("health")
                    diagnostic["hub_reachable"] = (
                        health.get("service") == "amh-native" and health.get("protocol") == 1
                    )
                except (OSError, ValueError) as exc:
                    diagnostic["hub_error"] = type(exc).__name__
            print(json.dumps(diagnostic, ensure_ascii=False))
            return
        if not explicit and not data_root:
            raise ValueError("persistent runtime state directory is unavailable")
        state = Path(explicit) if explicit else Path(str(data_root)) / "amh-native-sync"
        if not state.is_absolute() or state.is_symlink():
            raise ValueError("runtime state must be an absolute non-symlink directory")
        if (state / "paused").exists():
            if not args.enroll:
                print("{}")
            return
        policy = read_object(args.policy) if args.policy else {}
        from .worker import ensure_worker

        if args.enroll:
            enroll_runtime(state, policy, args.enroll)
            ensure_worker(state)
            return
        raw = sys.stdin.buffer.read(1_000_001)
        if len(raw) > 1_000_000:
            raise ValueError("Hook payload too large")
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise ValueError("Hook payload must be an object")
        if not (state / "config.json").exists():
            if policy:
                queue_observation(state, payload)
            project_root = os.environ.get("CLAUDE_PROJECT_DIR") or payload.get("cwd")
            supplied = os.environ.get("AMH_NATIVE_CONNECTION_FILE")
            if args.policy and (supplied or isinstance(project_root, str) and project_root):
                connection = (
                    Path(supplied) if supplied else Path(str(project_root)) / PAIRING_FILENAME
                )
                schedule_enrollment(state, args.policy, connection)
            print("{}")
            return
        validate_config(read_object(state / "config.json"), policy)
        queue_observation(state, payload)
        if (state / "paused").exists():
            print("{}")
            return
        if args.start_worker:
            ensure_worker(state)
        from .hooks import run

        print(json.dumps(run(state, "cowork", payload), ensure_ascii=False))
    except Exception as exc:
        if state and state.is_absolute() and not state.is_symlink():
            try:
                atomic_json(
                    state / "runtime-error.json", {"error": type(exc).__name__, "time": time.time()}
                )
            except OSError:
                pass
        if not args.enroll:
            print("{}")
    finally:
        DB_TIMEOUT.reset(timeout_context)


if __name__ == "__main__":
    main()
