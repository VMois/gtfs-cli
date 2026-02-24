"""Integration tests for the view command."""

import json
import tempfile
from pathlib import Path

from typer.testing import CliRunner

from gtfs_cli.commands.view import _process_snapshot
from gtfs_cli.engines.trip_update import TripUpdateDiffEngine
from gtfs_cli.main import app

runner = CliRunner()


def _make_snapshot(entities: list[dict], timestamp: int = 1000) -> dict:
    return {
        "header": {"gtfs_realtime_version": "2.0", "timestamp": timestamp},
        "entity": entities,
    }


def _make_trip_entity(entity_id: str, trip_id: str, route_id: str, stop_ids: list[str]) -> dict:
    return {
        "id": entity_id,
        "trip_update": {
            "trip": {"trip_id": trip_id, "route_id": route_id},
            "stop_time_update": [{"stop_id": sid} for sid in stop_ids],
        },
    }


def test_view_reads_ndjson_file():
    """Write a temp NDJSON file with 2 snapshots, verify view produces output."""
    snap1 = _make_snapshot([
        _make_trip_entity("1", "T1", "501", ["A", "B", "C"]),
    ])
    snap2 = _make_snapshot([
        _make_trip_entity("1", "T1", "501", ["B", "C"]),
    ], timestamp=1005)

    with tempfile.NamedTemporaryFile(mode="w", suffix=".ndjson", delete=False) as f:
        f.write(json.dumps(snap1) + "\n")
        f.write(json.dumps(snap2) + "\n")
        tmp_path = f.name

    result = runner.invoke(app, ["view", tmp_path])
    assert result.exit_code == 0
    # The output should contain departed stop A event
    assert "departed stop A" in result.output

    Path(tmp_path).unlink()


def test_view_no_source_reads_stdin():
    """Feed NDJSON via CliRunner input (simulates stdin)."""
    snap1 = _make_snapshot([
        _make_trip_entity("1", "T1", "501", ["A", "B"]),
    ])
    snap2 = _make_snapshot([
        _make_trip_entity("1", "T1", "501", ["B"]),
        _make_trip_entity("2", "T2", "502", ["X", "Y"]),
    ], timestamp=1010)

    ndjson_input = json.dumps(snap1) + "\n" + json.dumps(snap2) + "\n"
    result = runner.invoke(app, ["view"], input=ndjson_input)
    assert result.exit_code == 0
    assert "departed stop A" in result.output


def test_view_missing_file():
    result = runner.invoke(app, ["view", "/nonexistent/file.ndjson"])
    assert result.exit_code == 1


def test_process_snapshot_flicker_buffer():
    """Entity that disappears for fewer snapshots than buffer should not emit ended."""
    engine = TripUpdateDiffEngine()
    entity = _make_trip_entity("1", "T1", "501", ["A"])

    prev_entities: dict[str, dict] = {}
    absent_count: dict[str, int] = {}

    # Snapshot 1: entity appears
    snap1 = _make_snapshot([entity])
    events = _process_snapshot(snap1, engine, prev_entities, absent_count, buffer=3)
    assert any("started" in e.description for e in events)

    # Snapshot 2: entity disappears (absent_count = 1)
    snap2 = _make_snapshot([])
    events = _process_snapshot(snap2, engine, prev_entities, absent_count, buffer=3)
    assert not any("ended" in e.description for e in events)
    assert "T1" in absent_count

    # Snapshot 3: entity reappears (flicker) — should reset
    snap3 = _make_snapshot([entity])
    events = _process_snapshot(snap3, engine, prev_entities, absent_count, buffer=3)
    assert "T1" not in absent_count
    assert not any("ended" in e.description for e in events)


def test_process_snapshot_confirmed_removal():
    """Entity absent for >= buffer snapshots should emit ended."""
    engine = TripUpdateDiffEngine()
    entity = _make_trip_entity("1", "T1", "501", ["A"])

    prev_entities: dict[str, dict] = {}
    absent_count: dict[str, int] = {}

    # Snapshot 1: entity appears
    _process_snapshot(_make_snapshot([entity]), engine, prev_entities, absent_count, buffer=2)

    # Snapshot 2: entity gone (count=1)
    events = _process_snapshot(_make_snapshot([]), engine, prev_entities, absent_count, buffer=2)
    assert not any("ended" in e.description for e in events)

    # Snapshot 3: still gone (count=2 >= buffer) — should confirm removal
    events = _process_snapshot(_make_snapshot([]), engine, prev_entities, absent_count, buffer=2)
    assert any("ended" in e.description for e in events)
    assert "T1" not in prev_entities
