import json
import threading
from pathlib import Path
from unittest.mock import MagicMock

import httpx
import pytest
from google.transit import gtfs_realtime_pb2
from typer.testing import CliRunner

import gtfs_cli.commands.fetch as fetch_mod
from gtfs_cli.commands.fetch import _feed_to_geojson_dict, _feed_to_ndjson_line, _parse_feed
from gtfs_cli.main import app


class _ImmediateEvent(threading.Event):
    """Test double: records wait() timeouts and returns instantly without blocking."""

    def __init__(self):
        super().__init__()
        self.wait_times: list[float] = []

    def wait(self, timeout=None):
        if timeout is not None:
            self.wait_times.append(timeout)
        return self.is_set()

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


def test_watch_with_local_file_exits_with_error():
    """--watch only makes sense with URL sources, not local files."""
    result = runner.invoke(app, ["fetch", "--watch", "5", str(TRIP_UPDATE_PB)])
    assert result.exit_code == 1
    assert "URL source" in result.output


def test_watch_sigterm_exits_cleanly(monkeypatch):
    """SIGTERM causes the watch loop to exit without raising an exception."""
    import os
    import signal

    stop_event = _ImmediateEvent()
    call_count = 0

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            os.kill(os.getpid(), signal.SIGTERM)
        raise httpx.RequestError("network error")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert call_count >= 2


def test_watch_restores_sigterm_handler_after_exit(monkeypatch):
    """The original SIGTERM handler is restored after the loop exits."""
    import signal

    original_handler = signal.getsignal(signal.SIGTERM)
    stop_event = _ImmediateEvent()

    def mock_fetch(url, timeout, client=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert signal.getsignal(signal.SIGTERM) is original_handler


def test_remaining_sleep_returns_time_left(monkeypatch):
    from gtfs_cli.commands.fetch import _remaining_sleep
    monkeypatch.setattr("time.monotonic", lambda: 100.0)
    assert _remaining_sleep(105.0) == 5.0
    assert _remaining_sleep(100.0) == 0.0


def test_remaining_sleep_never_negative(monkeypatch):
    from gtfs_cli.commands.fetch import _remaining_sleep
    monkeypatch.setattr("time.monotonic", lambda: 100.0)
    assert _remaining_sleep(95.0) == 0.0


def test_watch_drift_corrected_sleep(monkeypatch):
    """Sleep duration is reduced by the time spent fetching."""
    call_count = 0
    stop_event = _ImmediateEvent()
    # interval=10: monotonic→0.0 sets next_wake=10; after fetch monotonic→7.0 → wait(3.0)
    # second iteration: monotonic→10.0 then fetch raises KeyboardInterrupt
    monotonic_seq = iter([0.0, 7.0, 10.0])

    monkeypatch.setattr("time.monotonic", lambda: next(monotonic_seq))

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        return b""

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr(fetch_mod, "_parse_feed", lambda _: MagicMock())
    monkeypatch.setattr(fetch_mod, "_feed_to_ndjson_line", lambda _: "{}")

    fetch_mod._watch_loop("https://example.com", 30.0, 10.0, _stop_event=stop_event)

    assert stop_event.wait_times == [3.0]  # 10.0 - 7.0 = 3.0


def test_backoff_delay_doubles_each_failure():
    from gtfs_cli.commands.fetch import _backoff_delay
    assert _backoff_delay(1) == 1.0
    assert _backoff_delay(2) == 2.0
    assert _backoff_delay(3) == 4.0
    assert _backoff_delay(4) == 8.0


def test_backoff_delay_capped_at_60():
    from gtfs_cli.commands.fetch import _backoff_delay
    assert _backoff_delay(10) == 60.0
    assert _backoff_delay(100) == 60.0


def test_backoff_delay_custom_cap():
    from gtfs_cli.commands.fetch import _backoff_delay
    assert _backoff_delay(10, cap=30.0) == 30.0


def test_watch_uses_backoff_sleep_on_consecutive_http_failures(monkeypatch):
    """Each successive HTTP failure sleeps for a longer backoff (1s, 2s, 4s…)."""
    call_count = 0
    stop_event = _ImmediateEvent()

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 4:
            raise KeyboardInterrupt
        raise httpx.RequestError("timeout")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert stop_event.wait_times == [1.0, 2.0, 4.0]


def test_watch_resets_backoff_after_success(monkeypatch):
    """A successful fetch resets the failure counter so backoff restarts from 1s."""
    call_count = 0
    stop_event = _ImmediateEvent()

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        # calls 1,2 fail; call 3 succeeds; call 4 fails; call 5 stops the loop
        if call_count in (1, 2, 4):
            raise httpx.RequestError("timeout")
        if call_count == 5:
            raise KeyboardInterrupt
        return b""

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr(fetch_mod, "_parse_feed", lambda _: MagicMock())
    monkeypatch.setattr(fetch_mod, "_feed_to_ndjson_line", lambda _: "{}")
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    # failure 1→backoff(1)=1, failure 2→backoff(2)=2, success→interval=5, failure 1→backoff(1)=1
    assert stop_event.wait_times == [1.0, 2.0, 5.0, 1.0]


def test_watch_parse_error_does_not_trigger_backoff(monkeypatch):
    """A parse error after a successful HTTP fetch does not increment the backoff counter."""
    call_count = 0
    stop_event = _ImmediateEvent()

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            raise KeyboardInterrupt
        return b""  # HTTP succeeds every time

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr(fetch_mod, "_parse_feed", lambda _: (_ for _ in ()).throw(RuntimeError("bad proto")))
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    # Both iterations should sleep the full interval, not a backoff
    assert stop_event.wait_times == [5.0, 5.0]


def test_watch_http_error_is_logged(monkeypatch, caplog):
    """HTTP errors are emitted via the logger at ERROR level."""
    import logging

    stop_event = _ImmediateEvent()
    call_count = 0

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        raise httpx.RequestError("connection timeout")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    with caplog.at_level(logging.ERROR, logger="gtfs_cli.commands.fetch"):
        fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert any("connection timeout" in r.message for r in caplog.records)
    assert all(r.levelno == logging.ERROR for r in caplog.records)


def test_watch_parse_error_is_logged(monkeypatch, caplog):
    """Parse errors are emitted via the logger at ERROR level."""
    import logging

    stop_event = _ImmediateEvent()
    call_count = 0

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        return b""

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr(fetch_mod, "_parse_feed", lambda _: (_ for _ in ()).throw(RuntimeError("bad proto")))
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    with caplog.at_level(logging.ERROR, logger="gtfs_cli.commands.fetch"):
        fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert any("bad proto" in r.message for r in caplog.records)


def test_watch_broken_pipe_exits_cleanly(monkeypatch):
    """BrokenPipeError on stdout causes a clean exit rather than a crash."""
    call_count = 0
    stop_event = _ImmediateEvent()

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        return b""

    class _BrokenStdout:
        def write(self, *args, **kwargs):
            raise BrokenPipeError("broken pipe")
        def flush(self, *args, **kwargs):
            raise BrokenPipeError("broken pipe")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr(fetch_mod, "_parse_feed", lambda _: MagicMock())
    monkeypatch.setattr(fetch_mod, "_feed_to_ndjson_line", lambda _: "{}")
    monkeypatch.setattr("sys.stdout", _BrokenStdout())
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert call_count == 1  # exits immediately after the broken pipe, not on the next iteration


def test_feed_to_ndjson_line_is_single_line():
    """_feed_to_ndjson_line should produce compact single-line JSON."""
    data = TRIP_UPDATE_PB.read_bytes()
    feed = _parse_feed(data)
    line = _feed_to_ndjson_line(feed)

    # Must be a single line (no embedded newlines)
    assert "\n" not in line

    # Must be valid JSON
    parsed = json.loads(line)
    assert "header" in parsed
    assert "entity" in parsed


def test_fetch_from_url_uses_provided_client():
    """_fetch_from_url calls client.get() instead of httpx.get() when a client is supplied."""
    fake_response = MagicMock()
    fake_response.content = b"data"
    fake_client = MagicMock()
    fake_client.get.return_value = fake_response

    result = fetch_mod._fetch_from_url("https://example.com", 30.0, client=fake_client)

    fake_client.get.assert_called_once_with("https://example.com")
    assert result == b"data"


def test_watch_reuses_http_client(monkeypatch):
    """_watch_loop creates one httpx.Client and passes the same instance to every fetch."""
    call_count = 0
    clients_seen = []
    stop_event = _ImmediateEvent()

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        clients_seen.append(client)
        if call_count >= 3:
            raise KeyboardInterrupt
        raise httpx.RequestError("simulated error")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, _stop_event=stop_event)

    assert call_count == 3
    assert clients_seen[0] is not None
    assert all(c is clients_seen[0] for c in clients_seen)


def test_feed_to_ndjson_line_preserves_proto_field_names():
    """NDJSON output should use snake_case field names like the pretty output."""
    data = TRIP_UPDATE_PB.read_bytes()
    feed = _parse_feed(data)
    parsed = json.loads(_feed_to_ndjson_line(feed))

    assert "gtfs_realtime_version" in parsed["header"]
    assert "gtfsRealtimeVersion" not in parsed["header"]


# ---------------------------------------------------------------------------
# GeoJSON helpers
# ---------------------------------------------------------------------------

def _make_vehicle_feed(vehicles: list[dict]) -> gtfs_realtime_pb2.FeedMessage:
    """Build a synthetic FeedMessage with vehicle position entities for testing."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    for v in vehicles:
        entity = feed.entity.add()
        entity.id = v.get("id", "v1")
        vp = entity.vehicle
        vp.trip.trip_id = v.get("trip_id", "")
        vp.trip.route_id = v.get("route_id", "")
        vp.position.latitude = v["lat"]
        vp.position.longitude = v["lon"]
        if "bearing" in v:
            vp.position.bearing = v["bearing"]
        if "speed" in v:
            vp.position.speed = v["speed"]
        if "timestamp" in v:
            vp.timestamp = v["timestamp"]
    return feed


def test_feed_to_geojson_dict_with_vehicles():
    feed = _make_vehicle_feed([
        {"id": "v1", "lat": 43.65, "lon": -79.38, "trip_id": "T1", "route_id": "R1", "bearing": 90.0, "speed": 12.5, "timestamp": 1700000000},
        {"id": "v2", "lat": 43.70, "lon": -79.42, "trip_id": "T2", "route_id": "R2"},
    ])
    result = _feed_to_geojson_dict(feed)

    assert result["type"] == "FeatureCollection"
    assert len(result["features"]) == 2

    f1 = result["features"][0]
    assert f1["type"] == "Feature"
    assert f1["geometry"]["type"] == "Point"
    # GeoJSON coordinates are [longitude, latitude]; proto float is 32-bit so use approx
    assert f1["geometry"]["coordinates"] == pytest.approx([-79.38, 43.65], rel=1e-4)
    assert f1["properties"]["vehicle_id"] == "v1"
    assert f1["properties"]["trip_id"] == "T1"
    assert f1["properties"]["route_id"] == "R1"
    assert f1["properties"]["bearing"] == pytest.approx(90.0, rel=1e-4)
    assert f1["properties"]["speed"] == pytest.approx(12.5, rel=1e-4)
    assert f1["properties"]["timestamp"] == 1700000000


def test_feed_to_geojson_dict_non_vehicle_feed():
    """A trip-update feed has no vehicle positions — output is an empty FeatureCollection."""
    data = TRIP_UPDATE_PB.read_bytes()
    feed = _parse_feed(data)
    result = _feed_to_geojson_dict(feed)

    assert result["type"] == "FeatureCollection"
    assert result["features"] == []


def test_feed_to_geojson_dict_skips_entity_without_position():
    """An entity that has a vehicle field but no position should be skipped."""
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.header.gtfs_realtime_version = "2.0"
    entity = feed.entity.add()
    entity.id = "v1"
    # Set vehicle but leave position unset
    entity.vehicle.trip.trip_id = "T1"

    result = _feed_to_geojson_dict(feed)
    assert result["features"] == []


def test_fetch_geojson_format_outputs_feature_collection():
    result = runner.invoke(app, ["fetch", "--format", "geojson", str(TRIP_UPDATE_PB)])
    assert result.exit_code == 0
    output = json.loads(result.output)
    assert output["type"] == "FeatureCollection"
    assert isinstance(output["features"], list)


def test_fetch_geojson_is_pretty_printed():
    """One-shot geojson output should be indented, not compact."""
    result = runner.invoke(app, ["fetch", "--format", "geojson", str(TRIP_UPDATE_PB)])
    assert "\n" in result.output


def test_watch_geojson_outputs_compact_ndjson(monkeypatch):
    """Watch mode with --format geojson should emit one compact GeoJSON line per iteration."""
    call_count = 0
    stop_event = _ImmediateEvent()
    captured_lines = []

    feed = _make_vehicle_feed([{"id": "v1", "lat": 43.65, "lon": -79.38}])

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 2:
            raise KeyboardInterrupt
        return feed.SerializeToString()

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.monotonic", lambda: 0.0)

    import builtins
    original_print = builtins.print

    def capture_print(*args, **kwargs):
        if kwargs.get("file") is None:
            captured_lines.append(args[0] if args else "")
        else:
            original_print(*args, **kwargs)

    monkeypatch.setattr(builtins, "print", capture_print)

    from gtfs_cli.commands.fetch import OutputFormat
    fetch_mod._watch_loop("https://example.com", 30.0, 5.0, OutputFormat.geojson, _stop_event=stop_event)

    assert len(captured_lines) == 1
    parsed = json.loads(captured_lines[0])
    assert parsed["type"] == "FeatureCollection"
    assert len(parsed["features"]) == 1
    assert parsed["features"][0]["geometry"]["coordinates"] == pytest.approx([-79.38, 43.65], rel=1e-4)
    # Must be a single compact line (no embedded newlines)
    assert "\n" not in captured_lines[0]
