import json
import sys
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import typer
from rich.live import Live
from rich.table import Table
from rich.text import Text

from gtfs_cli.engines.base import DiffEngine, Event
from gtfs_cli.engines.trip_update import TripUpdateDiffEngine


def _format_timestamp(ts: str) -> str:
    """Convert a UNIX timestamp string to local HH:MM:SS, or return as-is if not numeric."""
    if not ts:
        return "??:??:??"
    try:
        dt = datetime.fromtimestamp(int(ts), tz=timezone.utc).astimezone()
        return dt.strftime("%H:%M:%S %Z")
    except (ValueError, OSError):
        return ts


def _detect_engine(snapshot: dict) -> DiffEngine:
    """Auto-detect feed type from the first entity and return the right engine."""
    entities = snapshot.get("entity", [])
    if not entities:
        raise typer.BadParameter("First snapshot has no entities — cannot detect feed type.")

    first = entities[0]
    if "trip_update" in first:
        return TripUpdateDiffEngine()
    if "vehicle" in first:
        raise typer.BadParameter("Vehicle position engine is not implemented yet.")
    if "alert" in first:
        raise typer.BadParameter("Alert engine is not implemented yet.")

    raise typer.BadParameter("Cannot detect feed type from first entity.")


def _build_display(
    feed_type: str,
    trip_count: int,
    last_update: str,
    recent_events: deque[Event],
    trip_filter: str | None = None,
) -> Table:
    """Build a Rich Table used as the Live display content."""
    table = Table(show_header=False, show_edge=False, pad_edge=False, expand=True)
    table.add_column(ratio=1)

    table.add_row(Text(f"gtfs-cli view — {feed_type}", style="bold cyan"))
    if trip_filter:
        table.add_row(Text(f"Filtering trip {trip_filter}  |  Last update: {last_update}"))
    else:
        table.add_row(Text(f"Tracking {trip_count} trips  |  Last update: {last_update}"))
    table.add_row(Text(""))
    table.add_row(Text("Recent events:", style="bold"))

    if not recent_events:
        table.add_row(Text("  (waiting for changes...)", style="dim"))
    else:
        sorted_events = sorted(recent_events, key=lambda e: e.timestamp, reverse=True)
        for event in sorted_events:
            ts = _format_timestamp(event.timestamp)
            table.add_row(Text(f"  {ts}  {event.description}"))

    return table


def _entity_matches_trip(entity: dict, trip: str, engine: DiffEngine) -> bool:
    """Check if an entity matches a --trip filter value.

    Matches against the engine key (trip_id), the entity's top-level id,
    and the nested trip_id — covering all ways a user might reference a trip.
    """
    if engine.entity_key(entity) == trip:
        return True
    if entity.get("id") == trip:
        return True
    return False


def _process_snapshot(
    snapshot: dict,
    engine: DiffEngine,
    prev_entities: dict[str, dict],
    absent_count: dict[str, int],
    buffer: int,
) -> list[Event]:
    """Process one snapshot: diff against previous state, return new events.

    Updates prev_entities and absent_count in place.
    """
    current_entities: dict[str, dict] = {}
    for entity in snapshot.get("entity", []):
        key = engine.entity_key(entity)
        current_entities[key] = entity

    events: list[Event] = []

    # New and changed entities
    for key, entity in current_entities.items():
        # If it was in the absent tracker, it reappeared (flicker) — reset
        absent_count.pop(key, None)

        before = prev_entities.get(key)
        if before is None:
            # First time seeing this entity — emit "started" but skip stop
            # diff. The first snapshot is just a baseline; diffing stops
            # against it produces huge spurious departure counts.
            entity_events = engine.diff(None, entity)
        else:
            entity_events = engine.diff(before, entity)
        events.extend(entity_events)

    # Disappeared entities — apply flicker buffer
    for key in list(prev_entities.keys()):
        if key not in current_entities:
            absent_count[key] = absent_count.get(key, 0) + 1
            if absent_count[key] >= buffer:
                # Confirmed gone
                entity_events = engine.diff(prev_entities[key], None)
                events.extend(entity_events)
                del prev_entities[key]
                del absent_count[key]

    # Update prev_entities to current state (keep absent ones that haven't hit buffer)
    for key, entity in current_entities.items():
        prev_entities[key] = entity

    # Override event timestamps with the feed header timestamp.
    # Entity-level timestamps are often stale; the header timestamp
    # reflects when the producer actually generated this snapshot.
    header_ts = str(snapshot.get("header", {}).get("timestamp", ""))
    if header_ts:
        for event in events:
            event.timestamp = header_ts

    # Deduplicate: multiple entities can share the same trip_id but have
    # different entity ids, producing identical-looking events.
    seen_descriptions: set[str] = set()
    unique_events: list[Event] = []
    for event in events:
        if event.description not in seen_descriptions:
            seen_descriptions.add(event.description)
            unique_events.append(event)

    return unique_events


def view(
    source: Optional[Path] = typer.Argument(
        None,
        help="Path to an NDJSON file. If omitted, reads from stdin.",
    ),
    buffer: int = typer.Option(
        3,
        help="Number of consecutive absent snapshots before confirming a trip ended (flicker buffer).",
    ),
    trip: Optional[str] = typer.Option(
        None,
        help="Track a single trip by entity ID. Only events for this trip are shown.",
    ),
) -> None:
    """Display human-readable transit events from NDJSON snapshots.

    Reads NDJSON (one JSON-encoded GTFS-RT FeedMessage per line) and shows
    a live-updating terminal display of transit events like trip starts,
    trip ends, and stop departures.

    Examples:

        gtfs-cli fetch --watch 5 "https://gtfsrt.ttc.ca/trips/update?format=binary" | gtfs-cli view

        gtfs-cli view --trip 110290020 snapshots.ndjson

        gtfs-cli fetch --watch 5 "https://gtfsrt.ttc.ca/trips/update?format=binary" | gtfs-cli view --trip 110290020
    """
    if source is not None:
        if not source.exists():
            print(f"Error: file not found: {source}", file=sys.stderr)
            raise typer.Exit(code=1)
        input_stream = open(source)  # noqa: SIM115
    else:
        input_stream = sys.stdin

    engine: DiffEngine | None = None
    feed_type = "Unknown"
    prev_entities: dict[str, dict] = {}
    absent_count: dict[str, int] = {}
    recent_events: deque[Event] = deque(maxlen=50)
    snapshot_count = 0
    last_header_ts: str = ""

    try:
        with Live(
            _build_display(feed_type, 0, "--:--:--", recent_events, trip_filter=trip),
            refresh_per_second=2,
        ) as live:
            for line in input_stream:
                line = line.strip()
                if not line:
                    continue

                try:
                    snapshot = json.loads(line)
                except json.JSONDecodeError as e:
                    print(f"Warning: skipping invalid JSON line: {e}", file=sys.stderr)
                    continue

                # Auto-detect engine on first snapshot
                if engine is None:
                    engine = _detect_engine(snapshot)
                    if isinstance(engine, TripUpdateDiffEngine):
                        feed_type = "Trip Updates"

                # Skip if the producer hasn't updated the feed
                header_ts = str(snapshot.get("header", {}).get("timestamp", ""))
                if header_ts and header_ts == last_header_ts:
                    continue
                last_header_ts = header_ts

                # Filter to a single trip if --trip is set.
                # Matches against entity key (top-level id) OR the
                # trip_id nested inside the entity, since these can differ.
                if trip is not None:
                    snapshot = {
                        **snapshot,
                        "entity": [
                            e for e in snapshot.get("entity", [])
                            if _entity_matches_trip(e, trip, engine)
                        ],
                    }

                snapshot_count += 1
                events = _process_snapshot(
                    snapshot, engine, prev_entities, absent_count, buffer,
                )

                for event in events:
                    recent_events.appendleft(event)

                last_update = _format_timestamp(header_ts) if header_ts else "--:--:--"

                live.update(
                    _build_display(
                        feed_type,
                        len(prev_entities),
                        last_update,
                        recent_events,
                        trip_filter=trip,
                    )
                )

    except KeyboardInterrupt:
        pass
    finally:
        if source is not None and input_stream is not sys.stdin:
            input_stream.close()

    if snapshot_count == 0:
        print("No snapshots received.", file=sys.stderr)
