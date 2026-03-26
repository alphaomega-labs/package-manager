"""Tests for StateStore resilience to corrupted / empty / missing state files."""

from __future__ import annotations

import json
from pathlib import Path

from omegaxiv_manager.state import StateStore


def _make_store(tmp_path: Path, content: str | None = None) -> StateStore:
    state_file = tmp_path / "ox-state.json"
    if content is not None:
        state_file.write_text(content, encoding="utf-8")
    return StateStore(path=state_file)


def test_missing_file_returns_empty_installs(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    assert store.all() == []
    assert store.get("anything") is None


def test_empty_file_returns_empty_installs(tmp_path: Path) -> None:
    store = _make_store(tmp_path, "")
    assert store.all() == []


def test_whitespace_only_file_returns_empty_installs(tmp_path: Path) -> None:
    store = _make_store(tmp_path, "   \n\n  ")
    assert store.all() == []


def test_corrupted_json_returns_empty_and_backs_up(tmp_path: Path) -> None:
    state_file = tmp_path / "ox-state.json"
    store = _make_store(tmp_path, "{invalid json!!")
    result = store.all()
    assert result == []
    corrupted_backup = state_file.with_suffix(".json.corrupted")
    assert corrupted_backup.exists()
    assert corrupted_backup.read_text(encoding="utf-8") == "{invalid json!!"


def test_corrupted_json_does_not_overwrite_existing_backup(tmp_path: Path) -> None:
    state_file = tmp_path / "ox-state.json"
    corrupted_backup = state_file.with_suffix(".json.corrupted")
    corrupted_backup.write_text("old backup", encoding="utf-8")
    store = _make_store(tmp_path, "{bad}")
    store.all()
    assert corrupted_backup.read_text(encoding="utf-8") == "old backup"


def test_non_dict_json_returns_empty(tmp_path: Path) -> None:
    store = _make_store(tmp_path, '["not", "a", "dict"]')
    assert store.all() == []


def test_valid_state_roundtrips(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.upsert(
        handle="pkg",
        version="1.0.0",
        distribution_name="pkg-dist",
        install_target="pkg-dist",
        record_url="https://example.com/record.json",
        install_mode="global",
        venv_path=None,
        python_executable=None,
    )
    reloaded = StateStore(path=tmp_path / "ox-state.json")
    entries = reloaded.all()
    assert len(entries) == 1
    assert entries[0].handle == "pkg"
    assert entries[0].version == "1.0.0"


def test_upsert_after_corruption_writes_clean_state(tmp_path: Path) -> None:
    store = _make_store(tmp_path, "{bad json!!")
    store.all()  # triggers backup
    store.upsert(
        handle="fresh",
        version="2.0.0",
        distribution_name="fresh-dist",
        install_target="fresh-dist",
        record_url="https://example.com/r.json",
        install_mode="global",
        venv_path=None,
        python_executable=None,
    )
    state_file = tmp_path / "ox-state.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    assert "fresh" in data["installs"]
    assert data["installs"]["fresh"]["version"] == "2.0.0"
