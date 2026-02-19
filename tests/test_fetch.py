import json
from pathlib import Path

from typer.testing import CliRunner

from gtfs_cli.main import app

runner = CliRunner()

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TRIP_UPDATE_PB = FIXTURES_DIR / "ttc_trip_update_example.pb"


def test_fetch_local_file_outputs_json():
    result = runner.invoke(app, ["fetch", str(TRIP_UPDATE_PB)])
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert "header" in output
    assert "entity" in output


def test_fetch_local_file_has_feed_header():
    result = runner.invoke(app, ["fetch", str(TRIP_UPDATE_PB)])
    output = json.loads(result.output)
    header = output["header"]
    assert "gtfs_realtime_version" in header


def test_fetch_missing_file_exits_with_error():
    result = runner.invoke(app, ["fetch", "/nonexistent/path/feed.pb"])
    assert result.exit_code == 1


def test_fetch_no_args_shows_help():
    result = runner.invoke(app, ["fetch"])
    assert result.exit_code != 0


def test_fetch_preserves_proto_field_names():
    """Field names should use snake_case (proto style), not camelCase."""
    result = runner.invoke(app, ["fetch", str(TRIP_UPDATE_PB)])
    output = json.loads(result.output)
    header = output["header"]
    assert "gtfs_realtime_version" in header
    assert "gtfsRealtimeVersion" not in header
