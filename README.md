# gtfs-cli

CLI tool to fetch, process, and explore [GTFS](https://gtfs.org/) transit data — both realtime (GTFS-RT) and static schedules. Built on Unix philosophy: each command does one thing and writes to stdout, so they compose naturally with `jq`, `csvkit`, and each other.

## Installation

```bash
uv tool install gtfs-cli
```

After installation, the `gtfs-cli` command is available globally.

## Commands

### `static info`

Inspect a static GTFS feed (a folder of `.txt` files). Shows agency, valid dates, feed age, stop/route/trip counts, route types, service days, and file inventory.

```bash
# Download and unzip a feed, then inspect it
curl -L "https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/7795b45e-e65a-4465-81fc-c36b9dfff169/resource/cfb6b2b8-6191-41e3-bda1-b175c51148cb/download/TTC%20Routes%20and%20Schedules%20Data.zip" -o ttc.zip
unzip ttc.zip -d ttc/
gtfs-cli static info ttc/

# Inspect the current directory
gtfs-cli static info
```

For all available options, run:

```bash
gtfs-cli static info --help
```

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

For all available options, run:

```bash
gtfs-cli fetch --help
```

## Development

```bash
# Install dependencies
uv sync

# Run a command
uv run gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"

# Run tests
uv run pytest tests/ -v
```
