"""ONNX backend contract with stand-in runtime modules; no model download."""

import json
import sys
import types

import numpy as np
import pytest

from agent_brain.native_sync import semantic


class _Encoding:
    def __init__(self, text, specials):
        words = text.split()
        self.ids, self.offsets, cursor = [], [], 0
        for word in words:
            start = text.index(word, cursor)
            cursor = start + len(word)
            self.ids.append(10 + len(word))
            self.offsets.append((start, cursor))
        if specials:
            self.ids = [0, *self.ids, 2]


class _Tokenizer:
    @classmethod
    def from_file(cls, path):
        return cls()

    def no_truncation(self):
        pass

    def no_padding(self):
        pass

    def token_to_id(self, token):
        return 1 if token == "<pad>" else None

    def encode(self, text, add_special_tokens=True):
        return _Encoding(text, add_special_tokens)


class _Input:
    def __init__(self, name):
        self.name = name


class _Session:
    seen: list = []

    def __init__(self, path, options, providers):
        pass

    def get_inputs(self):
        return [_Input("input_ids"), _Input("attention_mask")]

    def run(self, outputs, feed):
        _Session.seen.append({k: v.copy() for k, v in feed.items()})
        ids = feed["input_ids"].astype(np.float32)
        hidden = np.stack([ids, np.ones_like(ids), np.full_like(ids, 3.0)], axis=-1)
        return [hidden]


@pytest.fixture
def fake_runtime(monkeypatch):
    ort = types.SimpleNamespace(
        SessionOptions=lambda: types.SimpleNamespace(),
        GraphOptimizationLevel=types.SimpleNamespace(ORT_ENABLE_ALL=99),
        InferenceSession=_Session,
    )
    monkeypatch.setitem(sys.modules, "onnxruntime", ort)
    monkeypatch.setitem(sys.modules, "tokenizers", types.SimpleNamespace(Tokenizer=_Tokenizer))
    _Session.seen = []


def _model(tmp_path, **manifest):
    (tmp_path / "onnx").mkdir()
    (tmp_path / "onnx/model.onnx").write_bytes(b"graph")
    (tmp_path / "tokenizer.json").write_text("{}")
    body = {"model_id": "test/e5", "onnx": "onnx/model.onnx", "pooling": "mean", **manifest}
    (tmp_path / semantic.MANIFEST).write_text(json.dumps(body))
    return tmp_path


def test_manifest_directory_loads_onnx_with_query_and_passage_prefixes(tmp_path, fake_runtime):
    backend = semantic.load_backend(
        str(_model(tmp_path, query_prefix="query: ", passage_prefix="passage: ", max_tokens=8))
    )
    assert isinstance(backend, semantic.LocalOnnxEncoder)
    assert backend.dim == 3 and backend.window_tokens == 5
    passage = backend.embed("alpha beta")
    query = semantic.query_vector(backend, "alpha beta")
    assert passage != query  # Different prefixes reach the model.
    assert abs(sum(v * v for v in passage) - 1) < 1e-6
    long_ids = _Session.seen[-1]["input_ids"]
    assert long_ids.shape[1] <= 8
    descriptor = semantic.descriptor(backend)
    assert descriptor["model"] == "test/e5" and descriptor["token_window"] == 5


def test_windows_are_source_slices_and_cover_the_text(tmp_path, fake_runtime):
    backend = semantic.load_backend(str(_model(tmp_path, max_tokens=6)))
    text = "one two three four five six seven eight nine"
    windows = backend.split_windows(text)
    assert all(text[w["start"] : w["end"]] == w["text"] for w in windows)
    assert windows[0]["start"] == 0 and windows[-1]["end"] == len(text)


def test_overlong_input_keeps_the_closing_special_token(tmp_path, fake_runtime):
    backend = semantic.load_backend(str(_model(tmp_path, max_tokens=5)))
    backend.embed("a bb ccc dddd eeeee ffffff")
    ids = _Session.seen[-1]["input_ids"][0]
    assert len(ids) == 5 and ids[-1] == 2


def test_manifest_cannot_point_outside_its_directory(tmp_path, fake_runtime):
    root = tmp_path / "m"
    root.mkdir()
    (tmp_path / "escape.onnx").write_bytes(b"x")
    (root / "tokenizer.json").write_text("{}")
    (root / semantic.MANIFEST).write_text(json.dumps({"onnx": "../escape.onnx"}))
    with pytest.raises(ValueError):
        semantic.load_backend(str(root))


def test_manifest_change_changes_the_fingerprint(tmp_path, fake_runtime):
    root = _model(tmp_path, query_prefix="query: ")
    first = semantic.load_backend(str(root)).model_version
    manifest = json.loads((root / semantic.MANIFEST).read_text())
    manifest["query_prefix"] = "q: "
    (root / semantic.MANIFEST).write_text(json.dumps(manifest))
    assert semantic.load_backend(str(root)).model_version != first
