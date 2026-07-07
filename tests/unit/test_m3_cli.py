"""Tests for M3 governance CLI commands."""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from agent_brain.interfaces.cli import app


@pytest.fixture
def runner():
    """Create a CLI test runner."""
    return CliRunner()


@pytest.fixture
def empty_brain_dir(tmp_path):
    """Create an empty brain directory for testing."""
    brain_dir = tmp_path / "brain"
    brain_dir.mkdir()
    items_dir = brain_dir / "items"
    items_dir.mkdir()
    return brain_dir


def test_govern_run_empty_store(runner, empty_brain_dir, monkeypatch):
    """Test govern run command with empty store."""
    monkeypatch.setenv("BRAIN_DIR", str(empty_brain_dir))
    
    # Mock the _open_components to avoid embedder initialization
    mock_store = MagicMock()
    mock_store.iter_all.return_value = iter([])
    
    with patch('agent_brain.interfaces.cli._open_components', return_value=(mock_store, MagicMock(), MagicMock())):
        result = runner.invoke(app, ["govern", "run"])
    
    # Should exit with code 0 (healthy) when no issues
    assert result.exit_code == 0
    assert "Governance Report" in result.stdout
    assert "**Scanned Items**: 0" in result.stdout
    assert "**Total Issues**: 0" in result.stdout


def test_anti_drift_empty_store(runner, empty_brain_dir, monkeypatch):
    """Test anti-drift command with empty store."""
    monkeypatch.setenv("BRAIN_DIR", str(empty_brain_dir))
    
    # Mock the _open_components to avoid embedder initialization
    mock_store = MagicMock()
    mock_store.iter_all.return_value = iter([])
    
    with patch('agent_brain.interfaces.cli._open_components', return_value=(mock_store, MagicMock(), MagicMock())):
        result = runner.invoke(app, ["anti-drift"])
    
    # Should exit with code 0 (clean) when no findings
    assert result.exit_code == 0
    assert "Anti-Drift Report" in result.stdout
    assert "**Scanned Items**: 0" in result.stdout
    assert "**Total Findings**: 0" in result.stdout


def test_inspect_nonexistent_item(runner, empty_brain_dir, monkeypatch):
    """Test inspect command with non-existent item."""
    monkeypatch.setenv("BRAIN_DIR", str(empty_brain_dir))
    
    # Mock the _open_components to avoid embedder initialization
    mock_store = MagicMock()
    mock_store.iter_all.return_value = iter([])
    
    with patch('agent_brain.interfaces.cli._open_components', return_value=(mock_store, MagicMock(), MagicMock())):
        result = runner.invoke(app, ["inspect", "nonexistent-item-id"])
    
    # Should exit with code 1 and show error
    assert result.exit_code == 1
    # Check either stdout or stderr for error message
    output = result.stdout + result.stderr
    assert "not found" in output.lower() or "error" in output.lower()


def test_govern_run_json_format(runner, empty_brain_dir, monkeypatch):
    """Test govern run command with JSON format."""
    monkeypatch.setenv("BRAIN_DIR", str(empty_brain_dir))
    
    # Mock the _open_components to avoid embedder initialization
    mock_store = MagicMock()
    mock_store.iter_all.return_value = iter([])
    
    with patch('agent_brain.interfaces.cli._open_components', return_value=(mock_store, MagicMock(), MagicMock())):
        result = runner.invoke(app, ["govern", "run", "--format", "json"])
    
    # Should exit with code 0 and output valid JSON
    assert result.exit_code == 0
    data = json.loads(result.stdout)
    assert "scanned_items" in data
    assert "total_issues" in data
    assert "healthy" in data
    assert data["scanned_items"] == 0
    assert data["total_issues"] == 0
    assert data["healthy"] is True
