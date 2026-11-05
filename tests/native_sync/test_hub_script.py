"""The one-command Hub installer ships with its release and refuses unverified files."""

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HUB = ROOT / "deploy/native-memory/hub.sh"
UPGRADE = ROOT / "deploy/native-memory/upgrade.sh"
WORKFLOW = ROOT / ".github/workflows/release-installers.yml"


def test_scripts_parse():
    for script in (HUB, UPGRADE):
        assert subprocess.run(["bash", "-n", str(script)]).returncode == 0


def test_release_bakes_version_and_ships_what_hub_sh_verifies():
    text = HUB.read_text()
    assert 'VERSION="__AMH_VERSION__"' in text and 'RELEASE_SHA="__AMH_RELEASE_SHA__"' in text
    for name in ("checksums.txt", "upgrade.sh", "semantic-model.json"):
        assert name in text
    flow = WORKFLOW.read_text()
    for name in ("hub.sh", "upgrade.sh", "semantic-model.json"):
        assert f"dist/{name}" in flow
    assert "__AMH_RELEASE_SHA__" in flow and "hub.sh upgrade.sh semantic-model.json" in flow


def test_upgrade_accepts_the_python_hub_sh_found():
    text = UPGRADE.read_text()
    assert "AMH_PYTHON" in text and "python3.11 -m venv" not in text


def test_hub_sh_stops_before_any_change_without_root_or_version(tmp_path):
    script = tmp_path / "hub.sh"
    script.write_text(HUB.read_text())
    result = subprocess.run(["bash", str(script)], capture_output=True, text=True)
    assert result.returncode != 0
    assert "root" in result.stderr or "version" in result.stderr


def _model_step() -> str:
    """The Python that upgrade.sh runs to fetch the pinned model."""
    text = UPGRADE.read_text()
    start = text.index("<<'PY'\n") + len("<<'PY'\n")
    return text[start : text.index("\nPY\n", start)]


def test_model_files_already_in_place_still_get_their_manifest(tmp_path):
    import hashlib
    import json
    import sys

    body = b"tokenizer bytes"
    spec = {
        "name": "m",
        "repo": "x/y",
        "revision": "0123456789abcdef",
        "files": [{"path": "tokenizer.json", "sha256": hashlib.sha256(body).hexdigest()}],
        "manifest": {"model_id": "x/y@test", "tokenizer": "tokenizer.json"},
    }
    (tmp_path / "semantic-model.json").write_text(json.dumps(spec))
    target = tmp_path / "models" / "m@0123456789ab"
    target.mkdir(parents=True)
    (target / "tokenizer.json").write_bytes(body)  # placed by an operator, no manifest
    result = subprocess.run(
        [sys.executable, "-", str(tmp_path / "semantic-model.json")],
        input=_model_step(),
        capture_output=True,
        text=True,
        env={"AMH_MODEL_ROOT": str(tmp_path / "models"), "PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(target)
    assert json.loads((target / "amh-semantic.json").read_text()) == spec["manifest"]


def test_model_files_can_come_from_a_local_directory_and_failure_is_spoken():
    text = HUB.read_text()
    assert "--model-dir" in text and '"$work/model/"' in text
    assert "semantic recall is NOT running" in text
    # upgrade.sh reads files staged beside the manifest before any mirror.
    assert 'staged = Path(sys.argv[1]).parent / "model"' in UPGRADE.read_text()


def _select(tmp_path, memory, choice=""):
    """Run the model step with a pinned small and large export, return (target, manifest)."""
    import hashlib
    import json
    import sys

    small, big, tok = b"small model", b"large model", b"tokenizer"
    sha = lambda data: hashlib.sha256(data).hexdigest()  # noqa: E731
    spec = {
        "name": "m-int8",
        "repo": "x/y",
        "revision": "0123456789abcdef",
        "files": [
            {"path": "onnx/small.onnx", "sha256": sha(small)},
            {"path": "tokenizer.json", "sha256": sha(tok)},
        ],
        "large": {
            "name": "m-fp32",
            "min_memory_mb": 3500,
            "min_available_mb": 1500,
            "files": [
                {"path": "onnx/big.onnx", "sha256": sha(big)},
                {"path": "tokenizer.json", "sha256": sha(tok)},
            ],
            "model_id": "x/y@fp32",
            "onnx": "onnx/big.onnx",
        },
        "manifest": {"model_id": "x/y@int8", "onnx": "onnx/small.onnx", "hook_gate": {"v": 1}},
    }
    (tmp_path / "semantic-model.json").write_text(json.dumps(spec))
    staged = tmp_path / "model" / "onnx"
    staged.mkdir(parents=True)
    (staged / "small.onnx").write_bytes(small)
    (staged / "big.onnx").write_bytes(big)
    (tmp_path / "model" / "tokenizer.json").write_bytes(tok)
    info = tmp_path / "meminfo"
    info.write_text(memory)
    env = {
        "AMH_MODEL_ROOT": str(tmp_path / "models"),
        "AMH_MEMINFO": str(info),
        "PATH": "/usr/bin:/bin",
    }
    if choice:
        env["AMH_SEMANTIC_MODEL"] = choice
    result = subprocess.run(
        [sys.executable, "-", str(tmp_path / "semantic-model.json")],
        input=_model_step(),
        capture_output=True,
        text=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr
    target = Path(result.stdout.strip())
    return target, json.loads((target / "amh-semantic.json").read_text())


BIG = "MemTotal: 33000000 kB\nMemAvailable: 26000000 kB\n"
AIHUB = "MemTotal: 1887436 kB\nMemAvailable: 1258291 kB\n"
BUSY = "MemTotal: 8000000 kB\nMemAvailable: 900000 kB\n"


def test_large_host_gets_the_float_export_and_keeps_the_gate(tmp_path):
    target, manifest = _select(tmp_path, BIG)
    assert target.name == "m-fp32@0123456789ab" and (target / "onnx/big.onnx").is_file()
    assert manifest["model_id"] == "x/y@fp32" and manifest["onnx"] == "onnx/big.onnx"
    assert manifest["hook_gate"] == {"v": 1}


def test_small_or_busy_host_stays_quantized(tmp_path):
    for name, memory in (("small", AIHUB), ("busy", BUSY), ("unreadable", "garbage")):
        root = tmp_path / name
        root.mkdir()
        target, manifest = _select(root, memory)
        assert target.name == "m-int8@0123456789ab" and manifest["model_id"] == "x/y@int8", name


def test_operator_choice_overrides_memory(tmp_path):
    small, large = tmp_path / "a", tmp_path / "b"
    small.mkdir()
    large.mkdir()
    assert _select(small, BIG, "small")[1]["model_id"] == "x/y@int8"
    assert _select(large, AIHUB, "large")[1]["model_id"] == "x/y@fp32"


def test_shipped_manifest_pins_both_exports_at_one_revision():
    import json

    spec = json.loads((ROOT / "deploy/native-memory/semantic-model.json").read_text())
    assert spec["name"].endswith("int8") and spec["large"]["name"].endswith("fp32")
    assert {f["path"] for f in spec["large"]["files"]} == {"onnx/model.onnx", "tokenizer.json"}
    assert spec["large"]["onnx"] == "onnx/model.onnx"
    assert all(len(f["sha256"]) == 64 for f in spec["large"]["files"])
    # hub.sh passes the choice through to upgrade.sh by environment.
    assert "AMH_SEMANTIC_MODEL" in UPGRADE.read_text() and "AMH_SEMANTIC_MODEL" in HUB.read_text()
