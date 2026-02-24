# `gtfs-cli diff` Command Design

## Purpose

Compare consecutive GTFS-RT snapshots and emit per-entity change events in **Debezium JSON format**. Designed to be piped from `gtfs-cli fetch --watch`. Each output record describes exactly one entity that was created, updated, or deleted between two snapshots.

This makes `diff` a **Change Data Capture (CDC)** layer for GTFS-RT feeds — downstream commands can consume a standardized change stream without knowing anything about GTFS-RT diffing.

## Usage

```
gtfs-cli diff [SOURCE] [OPTIONS]
```

`SOURCE` is a file path to an NDJSON file. If omitted, reads from stdin.

## Core Parameters

| Parameter | Description |
|-----------|-------------|
| `source` (argument, optional) | NDJSON file path. Defaults to stdin |
| `--buffer` | Number of snapshots to keep for flicker detection. Default: 3 |

## Input Format

Expects **NDJSON** — one complete JSON-encoded GTFS-RT FeedMessage per line. This is exactly what `gtfs-cli fetch --watch` outputs.

## Output Format: Debezium JSON

Each change is emitted as a single-line JSON object (NDJSON) following the [Debezium message envelope](https://debezium.io/documentation/reference/stable/connectors/postgresql.html#postgresql-change-events-value):

### Create (new entity appeared)

```json
{"before":null,"after":{"trip_id":"27823020","route_id":"503","stop_time_update":[...]},"source":{"feed_url":"https://gtfsrt.ttc.ca/trips/update?format=binary","feed_type":"trip_update","snapshot_ts":1589362330},"op":"c","ts_ms":1589362330904}
```

### Update (entity changed)

```json
{"before":{"trip_id":"27823020","route_id":"503","delay":60},"after":{"trip_id":"27823020","route_id":"503","delay":120},"source":{"feed_url":"https://gtfsrt.ttc.ca/trips/update?format=binary","feed_type":"trip_update","snapshot_ts":1589362330},"op":"u","ts_ms":1589362330904}
```

### Delete (entity removed, confirmed after flicker buffer)

```json
{"before":{"trip_id":"27823020","route_id":"503","delay":120},"after":null,"source":{"feed_url":"https://gtfsrt.ttc.ca/trips/update?format=binary","feed_type":"trip_update","snapshot_ts":1589362330},"op":"d","ts_ms":1589362330904}
```

### Field reference

| Field | Description |
|-------|-------------|
| `before` | Entity state in the previous snapshot. `null` for creates. |
| `after` | Entity state in the current snapshot. `null` for deletes. |
| `source.feed_url` | The URL the feed was fetched from (if available from input metadata). |
| `source.feed_type` | Detected feed type: `trip_update`, `vehicle`, or `alert`. |
| `source.snapshot_ts` | GTFS-RT `header.timestamp` of the snapshot that triggered this change. |
| `op` | Operation type: `c` (create), `u` (update), `d` (delete). |
| `ts_ms` | Wall-clock millisecond timestamp when `diff` emitted this record. |

## Entity Keying

Entities are keyed to track identity across snapshots:

| Feed type | Key |
|-----------|-----|
| `trip_update` | `entity.id` (falls back to `trip_update.trip.trip_id`) |
| `vehicle` | `entity.id` (falls back to `vehicle.vehicle.id`) |
| `alert` | `entity.id` |

## Architecture: fetch → diff pipeline

`fetch --watch` and `diff` communicate exclusively via NDJSON over a pipe. This keeps the two commands decoupled — `fetch` knows nothing about diffing, and `diff` knows nothing about protobuf or HTTP. The contract is: each line on stdin is a complete JSON-encoded GTFS-RT FeedMessage.

```
fetch --watch 5 <url>  ──NDJSON──▶  diff  ──Debezium JSON──▶  downstream
```

This means `diff` can also consume NDJSON from a file (saved snapshots), or from any other producer that emits the same format.

## Diff Engines

Each GTFS-RT feed type requires its own diff engine because the entity structure and meaningful changes differ between types. Diff engines share a common abstract interface so they can be developed and **unit tested independently** — no network, no CLI, just "given previous entities and current entities, produce change records."

### Abstract interface

```python
class DiffEngine(ABC):
    @abstractmethod
    def entity_key(self, entity: dict) -> str:
        """Extract a stable identity key from an entity."""

    @abstractmethod
    def diff(self, before: dict | None, after: dict | None) -> list[ChangeRecord]:
        """Compare two versions of the same entity, return change records.
        before=None means the entity is new. after=None means it was removed."""
```

The `diff` command holds the shared logic: reading NDJSON, keying entities, tracking the flicker buffer, and calling the appropriate engine. Each engine only needs to know how to key its entity type and how to compare two versions.

### Testing strategy

Because engines are plain Python classes with no I/O, they are straightforward to unit test:
- Construct `before`/`after` dicts from fixture data
- Call `engine.diff(before, after)`
- Assert on the returned change records (op type, before/after values)

No mocking of stdin, HTTP, or CLI needed. The flicker buffer and NDJSON reading are tested separately at the command level.

### Trip Update Engine (implementing first)

**Entity key:** `entity["id"]` (falls back to `entity["trip_update"]["trip"]["trip_id"]`)

**Changes detected:**
- New entity appeared → `op: "c"` with `before: null`
- Entity fields changed (stop_time_update list, delay values, schedule_relationship) → `op: "u"` with both `before` and `after`
- Entity removed (confirmed after flicker buffer) → `op: "d"` with `after: null`

### Future: Vehicle Position Engine

Keyed by vehicle id. Detect position changes, status changes, trip assignment changes.

### Future: Alert Engine

Keyed by alert id. Detect new alerts, cleared alerts, text/cause/effect changes.

### Feed type auto-detection

The feed type is detected from the first entity in the first snapshot by checking which field is present: `trip_update`, `vehicle`, or `alert`. The appropriate diff engine is selected once and used for the entire session.

## Flicker Buffer

GTFS-RT feeds are noisy — an entity might disappear for one snapshot and reappear in the next. To avoid false delete events, `diff` keeps a buffer of the last N snapshots (default: 3).

**Removal logic:**
- Entity present → absent (1 snapshot): mark as "maybe gone", do not emit
- Entity absent for 2 consecutive snapshots: confirmed gone, emit `op: "d"`
- Entity absent → reappeared: cancel, it was a flicker

Creates and updates are emitted immediately (less likely to flicker).

The buffer size is controlled by `--buffer` and defaults to 3.

## Pipeline Examples

```bash
# Stream trip update changes as Debezium JSON
gtfs-cli fetch --watch 5 "https://gtfsrt.ttc.ca/trips/update?format=binary" | gtfs-cli diff

# Filter only new trips
gtfs-cli fetch --watch 5 <url> | gtfs-cli diff | jq --unbuffered 'select(.op == "c")'

# Filter only deletes (ended trips)
gtfs-cli fetch --watch 5 <url> | gtfs-cli diff | jq --unbuffered 'select(.op == "d")'

# Extract delay changes
gtfs-cli fetch --watch 5 <url> | gtfs-cli diff | jq --unbuffered 'select(.op == "u") | {trip: .after.trip_id, delay: .after.delay}'

# Diff saved snapshots
gtfs-cli diff snapshots.ndjson

# Log all changes to a file
gtfs-cli fetch --watch 10 <url> | gtfs-cli diff >> changes.ndjson
```

## IMPLEMENTED

(nothing yet)

## POTENTIAL FEATURES
- `--gtfs <path>` to enrich `source` field with static GTFS data (stop names, route names)
- Vehicle position diff engine
- Alert diff engine
- Configurable change filters (e.g. `--min-delay 60` to only emit delay changes above a threshold)
- `--pretty` flag for human-readable change descriptions instead of Debezium JSON
