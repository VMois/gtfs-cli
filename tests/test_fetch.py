import json
from pathlib import Path
from unittest.mock import MagicMock

from typer.testing import CliRunner

import gtfs_cli.commands.fetch as fetch_mod
from gtfs_cli.commands.fetch import _feed_to_ndjson_line, _parse_feed
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


def test_watch_with_local_file_exits_with_error():
    """--watch only makes sense with URL sources, not local files."""
    result = runner.invoke(app, ["fetch", "--watch", "5", str(TRIP_UPDATE_PB)])
    assert result.exit_code == 1
    assert "URL source" in result.output


def test_watch_sigterm_exits_cleanly(monkeypatch):
    """SIGTERM causes the watch loop to exit without raising an exception."""
    import os
    import signal

    call_count = 0

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count == 2:
            os.kill(os.getpid(), signal.SIGTERM)
        raise RuntimeError("network error")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.sleep", lambda x: None)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0)  # must return, not raise

    assert call_count >= 2


def test_watch_restores_sigterm_handler_after_exit(monkeypatch):
    """The original SIGTERM handler is restored after the loop exits."""
    import signal

    original_handler = signal.getsignal(signal.SIGTERM)

    def mock_fetch(url, timeout, client=None):
        raise KeyboardInterrupt

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.sleep", lambda x: None)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0)

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
    sleep_times = []
    # interval=10: monotonic→0.0 sets next_wake=10; after fetch monotonic→7.0 → sleep 3.0
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
    monkeypatch.setattr("time.sleep", lambda x: sleep_times.append(x))

    fetch_mod._watch_loop("https://example.com", 30.0, 10.0)

    assert sleep_times == [3.0]  # 10.0 - 7.0 = 3.0


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


def test_watch_uses_backoff_sleep_on_consecutive_failures(monkeypatch):
    """Each successive failure sleeps for a longer backoff (1s, 2s, 4s…)."""
    call_count = 0
    sleep_times = []

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        if call_count >= 4:
            raise KeyboardInterrupt
        raise RuntimeError("network error")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.sleep", lambda x: sleep_times.append(x))

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0)

    assert sleep_times == [1.0, 2.0, 4.0]


def test_watch_resets_backoff_after_success(monkeypatch):
    """A successful fetch resets the failure counter so backoff restarts from 1s."""
    call_count = 0
    sleep_times = []

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        # calls 1,2 fail; call 3 succeeds; call 4 fails; call 5 stops the loop
        if call_count in (1, 2, 4):
            raise RuntimeError("network error")
        if call_count == 5:
            raise KeyboardInterrupt
        return b""

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr(fetch_mod, "_parse_feed", lambda _: MagicMock())
    monkeypatch.setattr(fetch_mod, "_feed_to_ndjson_line", lambda _: "{}")
    monkeypatch.setattr("time.sleep", lambda x: sleep_times.append(x))
    monkeypatch.setattr("time.monotonic", lambda: 0.0)  # next_wake always = interval

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0)

    # failure 1→backoff(1)=1, failure 2→backoff(2)=2, success→interval=5, failure 1→backoff(1)=1
    assert sleep_times == [1.0, 2.0, 5.0, 1.0]


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

    def mock_fetch(url, timeout, client=None):
        nonlocal call_count
        call_count += 1
        clients_seen.append(client)
        if call_count >= 3:
            raise KeyboardInterrupt
        raise RuntimeError("simulated error")

    monkeypatch.setattr(fetch_mod, "_fetch_from_url", mock_fetch)
    monkeypatch.setattr("time.sleep", lambda x: None)

    fetch_mod._watch_loop("https://example.com", 30.0, 5.0)

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
