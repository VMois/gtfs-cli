import sys
from pathlib import Path

import typer
from google.protobuf.json_format import MessageToJson
from google.transit import gtfs_realtime_pb2


def _is_url(source: str) -> bool:
    return source.startswith("http://") or source.startswith("https://")


def _fetch_from_url(url: str, timeout: float) -> bytes:
    import httpx

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


def fetch(
    source: str = typer.Argument(
        help="URL or local file path to a GTFS-RT protobuf feed.",
    ),
    timeout: float = typer.Option(
        30.0,
        help="HTTP request timeout in seconds (only applies to URL sources).",
    ),
) -> None:
    """Fetch GTFS-RT data and output as JSON.

    SOURCE is either an HTTP(S) URL or a local file path. Auto-detected.

    Examples:

        gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"

        gtfs-cli fetch trips.pb

        gtfs-cli fetch feed.pb | jq '.entity[] | .alert'
    """
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
