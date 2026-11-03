import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_native_installer_dry_run_reports_hub_without_touching_legacy(tmp_path):
    env = {**os.environ, "HOME": str(tmp_path)}
    r = subprocess.run(
        [
            "sh",
            str(ROOT / "install.sh"),
            "--native",
            "--hub",
            "https://team.example.com",
            "--dry-run",
        ],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    assert "https://team.example.com" in r.stdout
    assert "native" in r.stdout.lower()
    assert not (tmp_path / ".agent-memory-hub").exists()


def test_native_installer_requires_explicit_hub(tmp_path):
    r = subprocess.run(
        ["sh", str(ROOT / "install.sh"), "--native", "--dry-run"],
        cwd=ROOT,
        env={**os.environ, "HOME": str(tmp_path)},
        text=True,
        capture_output=True,
    )
    assert r.returncode != 0
    assert "--hub" in r.stderr


def test_published_installer_keeps_native_stage_and_wheel_on_same_release(tmp_path):
    # Exercise the remote dispatch without a checkout or network. The fake
    # second stage reports the download base inherited by its wheel fetch.
    script = tmp_path / "install.sh"
    script.write_text(
        (ROOT / "install.sh").read_text()
        .replace('RELEASE_REPO="__AMH_GITHUB_REPOSITORY__"', 'RELEASE_REPO="liuyang0508/agent-memory-hub"')
        .replace('RELEASE_REF="__AMH_GITHUB_REF_NAME__"', 'RELEASE_REF="v1.3.2"')
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    curl = bin_dir / "curl"
    curl.write_text(
        '#!/bin/sh\n'
        'printf "%s\\n" "$2" > "$DOWNLOAD_LOG"\n'
        'printf \'#!/bin/sh\\nprintf "base=%%s\\\\n" "$AMH_RELEASE_BASE"\\n\' > "$4"\n'
    )
    curl.chmod(0o755)
    env = {k: v for k, v in os.environ.items() if not k.startswith("AMH_")}
    env.update(
        PATH=str(bin_dir) + os.pathsep + os.environ["PATH"],
        DOWNLOAD_LOG=str(tmp_path / "download.log"),
        AMH_RELEASE_REF="v1.3.2",
    )
    r = subprocess.run(
        ["sh", str(script), "--org-memory", "--hub", "https://team.example.com"],
        cwd=tmp_path, env=env, text=True, capture_output=True,
    )
    assert r.returncode == 0, r.stderr
    base = "https://github.com/liuyang0508/agent-memory-hub/releases/download/v1.3.2"
    assert (tmp_path / "download.log").read_text().strip() == base + "/install-native.sh"
    assert r.stdout.strip() == "base=" + base


def test_ssh_options_reach_installed_setup_with_no_real_install(tmp_path):
    import pty
    import sys

    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    python = venv / "bin/python"
    python.write_text('#!/bin/sh\nexit 0\n')
    python.chmod(0o755)
    entry = venv / "bin/amh-native"
    entry.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > "$ARG_LOG"\n')
    entry.chmod(0o755)
    wheel = tmp_path / "synthetic.whl"
    wheel.touch()
    log = tmp_path / "args"
    master, slave = pty.openpty()
    try:
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), "--org-memory", "--hub", "https://example.com", "--ssh", "--web-port", "8080", "--no-service", "--wheel", str(wheel), "--venv", str(venv)],
            env={**os.environ, "HOME": str(tmp_path), "AMH_NATIVE_PYTHON": sys.executable, "ARG_LOG": str(log)},
            stdin=slave, capture_output=True, text=True,
        )
    finally:
        os.close(master)
        os.close(slave)
    assert result.returncode == 0, result.stderr
    args = log.read_text().splitlines()
    assert args[-3:] == ["--ssh", "--web-port", "8080"]
    assert args[:3] == ["setup", "--hub", "https://example.com"]
    assert "--no-service" in args


def test_installer_rejects_invalid_web_port_before_install(tmp_path):
    for port in ("-1", "65536", "oops", "0"):
        result = subprocess.run(
            ["sh", str(ROOT / "install.sh"), "--org-memory", "--hub", "https://example.com", "--ssh", "--web-port", port, "--dry-run"],
            env={**os.environ, "HOME": str(tmp_path)}, capture_output=True, text=True,
        )
        assert result.returncode != 0
        assert "--web-port" in result.stderr
    assert not (tmp_path / ".amh-native").exists()
