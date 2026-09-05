from __future__ import annotations

import json
from pathlib import Path

import pytest


@pytest.fixture
def db_path(tmp_path: Path) -> Path:
    return tmp_path / "test.db"


@pytest.fixture
def write_file(tmp_path: Path):
    """Write records to a JSON Lines file. Dicts are encoded; strings go verbatim."""

    def _write(records, name: str = "records.jsonl") -> Path:
        path = tmp_path / name
        lines = [r if isinstance(r, str) else json.dumps(r) for r in records]
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        return path

    return _write


@pytest.fixture
def record():
    """A valid record; override any field per test."""

    def _record(**overrides):
        base = {
            "id": "r-0001",
            "source": "alpha",
            "recordedAt": "2026-03-14T09:12:00Z",
            "value": 42,
            "status": "OK",
        }
        base.update(overrides)
        return base

    return _record
