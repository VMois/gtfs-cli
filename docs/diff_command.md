# `gtfs-cli diff` Command Design

## Purpose

Compare consecutive GTFS-RT snapshots and describe changes in plain language. Designed to be piped from `gtfs-cli fetch --watch` but also works standalone with saved snapshots.

## Usage

```
gtfs-cli diff [SOURCE] [OPTIONS]
```

`SOURCE` is a file path to an NDJSON file. If omitted, reads from stdin.

## Core Parameters

| Parameter | Description |
|-----------|-------------|
| `source` (argument, optional) | NDJSON file path. Defaults to stdin |
| `-i`, `--interactive` | Interactive terminal display using Rich Live |
| `--buffer` | Number of snapshots to keep for flicker detection. Default: 3 |

## Input Format

Expects **NDJSON** — one complete JSON-encoded GTFS-RT FeedMessage per line. This is exactly what `gtfs-cli fetch --watch` outputs.

## Output Modes

### Streaming (default)

One line per detected change, timestamped. Scrolls like a log. Pipeable to grep, files, scripts.

```
[14:32:05] trip 27823020 (route 503): passed stop 519
[14:32:05] trip 27823020 (route 503): delay updated at stop 7649 (+120s)
[14:32:10] trip 28001542 (route 63): new trip appeared
[14:32:35] trip 27810003 (route 501): trip ended
```

### Interactive (`-i`)

Rich Live dashboard that updates in place. Shows recent changes and summary. Not pipeable — designed for "leave it running on a monitor" use.

## Feed Type Auto-Detection

The feed type is detected from the first entity in the first snapshot by checking which field is present: `trip_update`, `vehicle`, or `alert`. Each feed type has its own diff engine.

### Starting with: Trip Updates

Trip update entities are keyed by `trip_id`.

**Changes detected:**
- Stop disappeared from `stop_time_update` list → "passed stop"
- Arrival/departure time changed → "delay updated"
- New trip entity appeared → "new trip appeared"
- Trip entity removed (confirmed after buffer) → "trip ended"

### Future: Vehicle Positions

Keyed by `vehicle_id`. Detect status changes, position jumps.

### Future: Alerts

Keyed by alert `id`. Detect new alerts, cleared alerts, text changes.

## Flicker Buffer

GTFS-RT feeds are noisy — an entity might disappear for one snapshot and reappear in the next. To avoid false "trip ended" events, `diff` keeps a buffer of the last N snapshots (default: 3).

**Removal logic:**
- Entity present → absent (1 snapshot): mark as "maybe gone", do not emit
- Entity absent for 2 consecutive snapshots: confirmed gone, emit "trip ended"
- Entity absent → reappeared: cancel, it was a flicker

Appearances and value changes are emitted immediately (less likely to flicker).

The buffer size is controlled by `--buffer` and defaults to 3.

## Pipeline Examples

```bash
# Stream trip update changes
gtfs-cli fetch --watch 5 "https://gtfsrt.ttc.ca/trips/update?format=binary" | gtfs-cli diff

# Interactive dashboard
gtfs-cli fetch --watch 5 "https://gtfsrt.ttc.ca/trips/update?format=binary" | gtfs-cli diff -i

# Diff saved snapshots
gtfs-cli diff snapshots.ndjson

# Filter only delays
gtfs-cli fetch --watch 5 <url> | gtfs-cli diff | grep "delay"

# Log changes to a file
gtfs-cli fetch --watch 10 <url> | gtfs-cli diff >> changes.log
```

## IMPLEMENTED

(nothing yet)

## POTENTIAL FEATURES
- `--gtfs <path>` to enrich output with static GTFS data (stop names, route names)
- Vehicle position diff engine
- Alert diff engine
- Interactive mode dashboard layout
- Configurable change filters (e.g. only show delays above a threshold)
