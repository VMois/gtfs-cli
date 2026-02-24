# `gtfs-cli view` Command Design

## Purpose

Live, interactive terminal display of GTFS-RT feed activity. Reads NDJSON snapshots from `gtfs-cli fetch --watch` and shows human-readable transit events using Rich Live — designed for demos, monitoring, and understanding what's happening on a transit network right now.

## Usage

```
gtfs-cli view [SOURCE]
```

`SOURCE` is a file path to an NDJSON file. If omitted, reads from stdin.

## Architecture: fetch → view pipeline

`fetch --watch` and `view` communicate exclusively via NDJSON over a pipe. `fetch` handles acquisition, `view` handles presentation. The contract is: each line on stdin is a complete JSON-encoded GTFS-RT FeedMessage.

```
fetch --watch 5 <url>  ──NDJSON──▶  view  ──Rich Live terminal──▶  user
```

`view` can also consume NDJSON from a file (replay saved snapshots).

## Core Parameters

| Parameter | Description |
|-----------|-------------|
| `source` (argument, optional) | NDJSON file path. Defaults to stdin |
| `--buffer` | Number of snapshots to keep for flicker detection. Default: 3 |

## Input Format

Expects **NDJSON** — one complete JSON-encoded GTFS-RT FeedMessage per line. This is exactly what `gtfs-cli fetch --watch` outputs.

## Output: Interactive Terminal (Rich Live)

The display updates in place using Rich Live. Not pipeable — this is a terminal UI for humans.

### Feed type auto-detection

The feed type is detected from the first entity in the first snapshot by checking which field is present: `trip_update`, `vehicle`, or `alert`. The appropriate diff engine is selected once and used for the entire session.

## Diff Engines

Each GTFS-RT feed type requires its own diff engine because the entity structure and what constitutes a meaningful, human-readable event differs between types. Diff engines share a common abstract interface so they can be developed and **unit tested independently** — no network, no CLI, just "given previous entities and current entities, produce event descriptions."

### Abstract interface

```python
class DiffEngine(ABC):
    @abstractmethod
    def entity_key(self, entity: dict) -> str:
        """Extract a stable identity key from an entity."""

    @abstractmethod
    def diff(self, before: dict | None, after: dict | None) -> list[Event]:
        """Compare two versions of the same entity, return human-readable events.
        before=None means the entity is new. after=None means it was removed."""
```

The `view` command holds the shared logic: reading NDJSON, keying entities, tracking the flicker buffer, and calling the appropriate engine. Each engine only needs to know how to key its entity type and how to interpret changes.

### Testing strategy

Because engines are plain Python classes with no I/O, they are straightforward to unit test:
- Construct `before`/`after` dicts from fixture data
- Call `engine.diff(before, after)`
- Assert on the returned events (type, description)

No mocking of stdin, HTTP, or CLI needed. The flicker buffer and NDJSON reading are tested separately at the command level.

### Trip Update Engine (implementing first)

**Entity key:** `entity["id"]` (falls back to `entity["trip_update"]["trip"]["trip_id"]`)

**Events detected:**

| What happened | How it's detected | Display |
|---------------|-------------------|---------|
| Trip started | Entity key not in previous snapshot | `Trip 27823020 (route 503) started` |
| Trip ended | Entity key gone (confirmed after flicker buffer) | `Trip 27823020 (route 503) ended` |
| Vehicle departed stop | Stop disappeared from `stop_time_update` list | `Trip 27823020 (route 503) departed stop 519` |
| Delay changed | `arrival.delay` or `departure.delay` changed at a stop | `Trip 27823020 (route 503) delay at stop 7649: +120s (was +60s)` |

The engine compares `stop_time_update` lists between before/after to detect stop-level changes. This is GTFS-RT-specific intelligence — the engine understands transit semantics, not just data diffs.

### Future: Vehicle Position Engine

Keyed by vehicle id. Events: vehicle moved, status changed (IN_TRANSIT_TO → STOPPED_AT), trip assignment changed.

### Future: Alert Engine

Keyed by alert id. Events: new alert issued, alert cleared, alert text/cause/effect changed.

## Flicker Buffer

GTFS-RT feeds are noisy — an entity might disappear for one snapshot and reappear in the next. To avoid false "trip ended" events, `view` keeps a buffer of the last N snapshots (default: 3).

**Removal logic:**
- Entity present → absent (1 snapshot): mark as "maybe gone", do not emit
- Entity absent for 2 consecutive snapshots: confirmed gone, emit "trip ended"
- Entity absent → reappeared: cancel, it was a flicker

New trips and stop-level changes are emitted immediately (less likely to flicker).

The buffer size is controlled by `--buffer` and defaults to 3.

## Example Output

```
gtfs-cli view — Trip Updates (https://gtfsrt.ttc.ca/trips/update)
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
Tracking 847 trips  |  Last update: 14:32:05

Recent events:
  14:32:05  Trip 27823020 (route 503) departed stop 519
  14:32:05  Trip 27823020 (route 503) delay at stop 7649: +120s (was +60s)
  14:32:10  Trip 28001542 (route 63) started
  14:32:35  Trip 27810003 (route 501) ended
  14:31:55  Trip 27799101 (route 504) departed stop 1044
  14:31:55  Trip 27799101 (route 504) delay at stop 1100: +45s (was +30s)
  ...
```

## Pipeline Examples

```bash
# Live dashboard of trip update activity
gtfs-cli fetch --watch 5 "https://gtfsrt.ttc.ca/trips/update?format=binary" | gtfs-cli view

# Replay saved snapshots
gtfs-cli view snapshots.ndjson
```

## IMPLEMENTED

(nothing yet)

## POTENTIAL FEATURES
- `--gtfs <path>` to enrich display with static GTFS data (stop names, route names instead of IDs)
- Vehicle position engine
- Alert engine
- Summary statistics panel (trips per route, average delay)
- Filter by route (`--route 503`)
- Color coding by event type or delay severity
