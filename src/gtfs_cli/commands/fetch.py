import enum
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


class OutputFormat(str, enum.Enum):
    json = "json"
    geojson = "geojson"
    geojsonl = "geojsonl"


class FeedType(enum.Enum):
    VEHICLE_POSITIONS = "vehicle_positions"
    TRIP_UPDATES = "trip_updates"
    ALERTS = "alerts"
    MIXED = "mixed"
    UNKNOWN = "unknown"

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


def _detect_feed_type(feed: gtfs_realtime_pb2.FeedMessage) -> FeedType:
    """Infer feed type from the first entity's populated field.

    GTFS-RT feeds are almost always homogeneous (all entities are the same
    type), so inspecting the first entity is sufficient. Returns UNKNOWN for
    empty feeds and MIXED when the first two entities disagree.
    """
    detected: FeedType | None = None
    for entity in feed.entity:
        if entity.HasField("vehicle"):
            kind = FeedType.VEHICLE_POSITIONS
        elif entity.HasField("trip_update"):
            kind = FeedType.TRIP_UPDATES
        elif entity.HasField("alert"):
            kind = FeedType.ALERTS
        else:
            continue

        if detected is None:
            detected = kind
        elif detected != kind:
            return FeedType.MIXED
        else:
            break  # two entities agree — no need to scan further

    return detected if detected is not None else FeedType.UNKNOWN


def _check_geojson_compatible(feed: gtfs_realtime_pb2.FeedMessage) -> None:
    """Raise ValueError if the feed is not a vehicle position feed."""
    feed_type = _detect_feed_type(feed)
    if feed_type not in (FeedType.VEHICLE_POSITIONS, FeedType.UNKNOWN):
        raise ValueError(
            f"Feed contains {feed_type.value}; geojson/geojsonl formats only support vehicle position feeds."
        )


def _feed_to_geojson_features(feed: gtfs_realtime_pb2.FeedMessage) -> list[dict]:
    """Extract vehicle positions from a FeedMessage as a list of GeoJSON Feature dicts.

    Entities without a vehicle position are silently skipped.
    """
    features = []
    for entity in feed.entity:
        if not entity.HasField("vehicle"):
            continue
        vehicle = entity.vehicle
        if not vehicle.HasField("position"):
            continue
        position = vehicle.position
        features.append(
            {
                "type": "Feature",
                "geometry": {
                    "type": "Point",
                    # GeoJSON uses [longitude, latitude] order per RFC 7946
                    "coordinates": [position.longitude, position.latitude],
                },
                "properties": {
                    "vehicle_id": entity.id,
                    "trip_id": vehicle.trip.trip_id,
                    "route_id": vehicle.trip.route_id,
                    "bearing": position.bearing,
                    "speed": position.speed,
                    "timestamp": vehicle.timestamp,
                },
            }
        )
    return features


def _features_to_feature_collection(features: list[dict]) -> dict:
    """Wrap a list of GeoJSON Feature dicts in a FeatureCollection."""
    return {"type": "FeatureCollection", "features": features}


def _features_to_geojsonl(features: list[dict]) -> str:
    """Serialize GeoJSON features to GeoJSONL: one minified Feature per line (RFC 8142)."""
    return "\n".join(json.dumps(f, separators=(",", ":")) for f in features)


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
    output_format: OutputFormat = typer.Option(
        OutputFormat.json,
        "--format",
        help="Output format. geojson and geojsonl extract vehicle positions only.",
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

        gtfs-cli fetch --format geojson "https://gtfsrt.ttc.ca/vehicles/position?format=binary"

        gtfs-cli fetch --format geojson --watch 30 "https://gtfsrt.ttc.ca/vehicles/position?format=binary"

        gtfs-cli fetch --format geojsonl "https://gtfsrt.ttc.ca/vehicles/position?format=binary"

        gtfs-cli fetch --format geojsonl --watch 30 "https://gtfsrt.ttc.ca/vehicles/position?format=binary"
    """
    if watch is not None:
        if not _is_url(source):
            print(
                "Error: --watch requires a URL source (watching a local file is not supported).",
                file=sys.stderr,
            )
            raise typer.Exit(code=1)
        _watch_loop(source, timeout, watch, output_format)
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

    if output_format in (OutputFormat.geojson, OutputFormat.geojsonl):
        try:
            _check_geojson_compatible(feed)
        except ValueError as e:
            print(f"Error: {e}", file=sys.stderr)
            raise typer.Exit(code=1)
        features = _feed_to_geojson_features(feed)
        if output_format == OutputFormat.geojson:
            print(json.dumps(_features_to_feature_collection(features), indent=2))
        else:
            print(_features_to_geojsonl(features))
    else:
        print(MessageToJson(feed, preserving_proto_field_name=True))


def _watch_loop(
    url: str,
    timeout: float,
    interval: float,
    output_format: OutputFormat = OutputFormat.json,
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
                    if output_format in (OutputFormat.geojson, OutputFormat.geojsonl):
                        _check_geojson_compatible(feed)
                        features = _feed_to_geojson_features(feed)
                        if output_format == OutputFormat.geojson:
                            line = json.dumps(_features_to_feature_collection(features), separators=(",", ":"))
                        else:
                            line = _features_to_geojsonl(features)
                    else:
                        line = _feed_to_ndjson_line(feed)
                    print(line)
                    sys.stdout.flush()
                except ValueError as e:
                    logger.error("Format error: %s", e)
                    stop_event.set()
                    break
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
