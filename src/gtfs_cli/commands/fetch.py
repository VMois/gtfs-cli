import json
import logging
import signal
import sys
import threading
import time
from pathlib import Path
from typing import Optional

import typer
from google.protobuf.json_format import MessageToDict, MessageToJson
from google.transit import gtfs_realtime_pb2

logging.basicConfig(
    level=logging.ERROR,
    format="%(asctime)s %(levelname)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _fetch_from_url(url: str, timeout: float, client=None) -> bytes:
    import httpx

    if client is not None:
        response = client.get(url)
    else:
        response = httpx.get(url, timeout=timeout, follow_redirects=True)
    response.raise_for_status()
    return response.content


def _read_from_file(path: str) -> bytes:
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return file_path.read_bytes()


def _parse_feed(data: bytes) -> gtfs_realtime_pb2.FeedMessage:
    feed = gtfs_realtime_pb2.FeedMessage()
    feed.ParseFromString(data)
    return feed


def _remaining_sleep(next_wake: float) -> float:
    """Seconds until next_wake on the monotonic clock. Never negative."""
    return max(0.0, next_wake - time.monotonic())


def _backoff_delay(consecutive_failures: int, cap: float = 60.0) -> float:
    """Exponential backoff: 1s, 2s, 4s, …, capped at `cap` seconds."""
    return min(2.0 ** (consecutive_failures - 1), cap)


def _feed_to_ndjson_line(feed: gtfs_realtime_pb2.FeedMessage) -> str:
    """Convert a FeedMessage to a single-line JSON string (for NDJSON output)."""
    d = MessageToDict(feed, preserving_proto_field_name=True)
    return json.dumps(d, separators=(",", ":"))


def fetch(
    source: str = typer.Argument(
        help="URL or local file path to a GTFS-RT protobuf feed.",
    ),
    timeout: float = typer.Option(
        30.0,
        help="HTTP request timeout in seconds (only applies to URL sources).",
    ),
    watch: Optional[float] = typer.Option(
        None,
        help="Continuously fetch at this interval (seconds). Outputs NDJSON. URL sources only.",
    ),
) -> None:
    """Fetch GTFS-RT data and output as JSON.

    SOURCE is either an HTTP(S) URL or a local file path. Auto-detected.

    Examples:

        gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"

        gtfs-cli fetch trips.pb

        gtfs-cli fetch feed.pb | jq '.entity[] | .alert'

        gtfs-cli fetch --watch 30 "https://gtfsrt.ttc.ca/trips/update?format=binary"

        gtfs-cli fetch --watch 30 "https://gtfsrt.ttc.ca/trips/update?format=binary" | jq --unbuffered '.entity | length'
    """
    if watch is not None:
        if not _is_url(source):
            print(
                "Error: --watch requires a URL source (watching a local file is not supported).",
                file=sys.stderr,
            )
            raise typer.Exit(code=1)
        _watch_loop(source, timeout, watch)
        return

    try:
        if _is_url(source):
            data = _fetch_from_url(source, timeout)
        else:
            data = _read_from_file(source)
    except FileNotFoundError as e:
        print(str(e), file=sys.stderr)
        raise typer.Exit(code=1)
    except Exception as e:
        print(f"Error fetching source: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    try:
        feed = _parse_feed(data)
    except Exception as e:
        print(f"Error parsing protobuf: {e}", file=sys.stderr)
        raise typer.Exit(code=1)

    json_output = MessageToJson(feed, preserving_proto_field_name=True)
    print(json_output)


def _watch_loop(
    url: str,
    timeout: float,
    interval: float,
    _stop_event: threading.Event | None = None,
) -> None:
    """Continuously fetch a GTFS-RT feed and output NDJSON lines."""
    import httpx

    stop_event = _stop_event if _stop_event is not None else threading.Event()

    def _sigterm_handler(signum, frame):
        stop_event.set()

    old_handler = signal.signal(signal.SIGTERM, _sigterm_handler)
    consecutive_failures = 0
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            while not stop_event.is_set():
                next_wake = time.monotonic() + interval
                try:
                    data = _fetch_from_url(url, timeout, client=client)
                except (httpx.HTTPStatusError, httpx.RequestError) as e:
                    logger.error("HTTP error: %s", e)
                    consecutive_failures += 1
                    stop_event.wait(_backoff_delay(consecutive_failures))
                    continue

                consecutive_failures = 0
                try:
                    feed = _parse_feed(data)
                    print(_feed_to_ndjson_line(feed))
                    sys.stdout.flush()
                except BrokenPipeError:
                    stop_event.set()
                    break
                except Exception as e:
                    logger.error("Parse error: %s", e)

                stop_event.wait(_remaining_sleep(next_wake))
    except KeyboardInterrupt:
        pass
    finally:
        signal.signal(signal.SIGTERM, old_handler)
