# gtfs-cli

CLI tool to fetch, archive, process and explore [GTFS-RT](https://gtfs.org/documentation/realtime/reference/) (General Transit Feed Specification — Realtime) data. GTFS-RT feeds provide live transit information: trip updates, vehicle positions, and service alerts in protobuf format.

## Installation

```bash
uv tool install gtfs-cli
```

Or with pip:

```bash
pip install gtfs-cli
```

After installation, the `gtfs-cli` command is available globally.

## Commands

### `fetch`

Fetch a GTFS-RT feed from a URL or local file and output it as JSON.

```bash
# Fetch live trip updates
gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"

# Fetch vehicle positions
gtfs-cli fetch "https://gtfsrt.ttc.ca/vehicles/position?format=binary"

# Fetch service alerts
gtfs-cli fetch "https://gtfsrt.ttc.ca/alerts/all?format=binary"

# Inspect a previously saved .pb file
gtfs-cli fetch trips.pb
```

**Filtering with jq:**

```bash
# List all active alerts
gtfs-cli fetch "https://gtfsrt.ttc.ca/alerts/all?format=binary" | jq '.entity[] | .alert'

# Count entities in a feed
gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary" | jq '.entity | length'

# Extract all trip IDs
gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary" \
  | jq '[.entity[].trip_update.trip.trip_id]'
```

**Watch mode** — continuously poll a feed and stream NDJSON (one JSON object per line):

```bash
# Poll every 30 seconds
gtfs-cli fetch --watch 30 "https://gtfsrt.ttc.ca/trips/update?format=binary"

# Count entities on each snapshot
gtfs-cli fetch --watch 30 "https://gtfsrt.ttc.ca/trips/update?format=binary" \
  | jq --unbuffered '.entity | length'

# Save a long-running collection to a file
gtfs-cli fetch --watch 30 "https://gtfsrt.ttc.ca/trips/update?format=binary" \
  >> snapshots.ndjson
```

Watch mode handles transient failures gracefully: HTTP and network errors are retried with exponential backoff (1s → 2s → 4s … capped at 60s). Stop with `Ctrl+C` or `SIGTERM`.

**Options:**

| Option | Default | Description |
|--------|---------|-------------|
| `--timeout` | `30.0` | HTTP request timeout in seconds |
| `--watch` | — | Poll interval in seconds (URL sources only) |

## Development

```bash
# Install dependencies
uv sync

# Run a command
uv run gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"

# Run tests
uv run pytest tests/ -v
```
