# `gtfs-cli fetch` Command Design

## Purpose

Load GTFS-RT data from any source (URL or local file) and output it in a human-readable format. This is the single entry point for accessing GTFS-RT data — it handles acquisition and decoding as one cohesive step.

## Usage

```
gtfs-cli fetch <source> [OPTIONS]
```

`source` is either an HTTP(S) URL or a local file path. Auto-detected.

## Core Parameters

| Parameter | Description |
|-----------|-------------|
| `source` (argument) | URL or local file path to a GTFS-RT protobuf |

## Output Behavior

- **JSON to stdout by default** — human-readable and machine-parseable
- Errors and status info go to stderr, never stdout (keeps pipes clean)
- Exit code 0 on success, non-zero on HTTP/network/parse errors

### Future: Smart Output Detection

When stdout is a **terminal**, output JSON (human-readable). When stdout is a **pipe**, output length-delimited protobuf (efficient binary streaming between gtfs-cli commands). `--format` overrides auto-detection. This avoids wasteful proto-to-JSON-to-proto conversion in pipelines while keeping interactive use friendly.

## Source Detection

- Starts with `http://` or `https://` — treated as URL, fetched via HTTP
- Otherwise — treated as a local file path, read from disk
- This means saved snapshots and live feeds use the exact same interface

## Examples

```bash
# Quick look at a live feed (JSON output)
gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"

# Inspect a previously saved file
gtfs-cli fetch trips.pb

# Pipe JSON to jq for quick filtering
gtfs-cli fetch "https://gtfsrt.ttc.ca/alerts/all?format=binary" | jq '.entity[] | .alert'
```

## Use Cases

- **Quick inspection**: `gtfs-cli fetch <url>` to see what a feed looks like right now.
- **Debugging saved files**: same command works on URLs and local files.
- **Pipeline integration**: JSON output pipes cleanly into jq, scripts, or future gtfs-cli commands.

## IMPLEMENTED
- `fetch <source>` — URL or local file
- JSON output to stdout
- `--timeout` for HTTP sources
- `--watch <seconds>` with NDJSON streaming, flush-after-write, graceful SIGINT handling

## Watch Mode

`--watch <seconds>` enables continuous fetching at a fixed interval. Each fetch produces one complete JSON object, output as **NDJSON (Newline-Delimited JSON)** — one JSON document per line, flushed immediately after each write.

```bash
# Fetch trip updates every 30 seconds, filter with jq
gtfs-cli fetch --watch 30 "https://gtfsrt.ttc.ca/trips/update?format=binary" | jq --unbuffered '.entity[]'
```

### Why NDJSON

- Each line is a self-contained JSON document — consumers parse line by line
- `jq` handles this natively, no special flags needed to parse (use `--unbuffered` to avoid jq's own buffering)
- Python consumers: `for line in sys.stdin: data = json.loads(line)`
- Plain concatenated JSON (`{}{}`) is not valid JSON — NDJSON uses `\n` as a delimiter to avoid this

### Implementation Notes

- Flush stdout after each JSON write so data reaches the consumer immediately
- Graceful SIGINT (Ctrl+C) handling — stop cleanly, exit 0
- Only applies to URL sources (watching a local file doesn't make sense)

## POTENTIAL FEATURES
- `--format binary|table` output options
- `--output` file writing
- TTY auto-detection for output format (JSON for terminal, length-delimited protobuf for pipes)

### Watch reliability improvements

These improvements target long-running `--watch` sessions (hours/days of data collection):

- **Persistent HTTP client** — reuse a single `httpx.Client` with connection pooling instead of creating a new connection per request. Reduces TCP/TLS handshake overhead and is kinder to the server.
- **Exponential backoff on consecutive failures** — on transient errors (timeout, 503), increase sleep between retries (e.g. 1s → 2s → 4s → …, capped). Reset backoff on success. Avoids hammering a struggling server.
- **SIGTERM handling** — catch `SIGTERM` (via `signal` module) for clean shutdown when run as a systemd service or in Docker. Currently only `KeyboardInterrupt` (SIGINT) is handled.
- **Drift-corrected sleep** — instead of `time.sleep(interval)` after each fetch (which drifts by fetch duration), compute the next wall-clock target and sleep until that time. Keeps spacing consistent.

### Output durability for long-running collection

For serious data collection, NDJSON-to-stdout has limits — a crash mid-write can produce partial lines, and restarts lose context. Two durable storage options:

**SQLite (`--output collection.db`)**
- Append each snapshot as a row with a timestamp. Inherently atomic per transaction — no partial writes.
- Good for: moderate volume, easy querying (`SELECT * WHERE timestamp > ...`), single-file portability.
- Schema: `(id INTEGER PRIMARY KEY, fetched_at TEXT, feed_json TEXT)` or normalized tables for entities.
- Downside: JSON-in-a-column isn't great for columnar analytics. File can grow large without vacuuming.

**Parquet (`--output collection.parquet` or `--output-dir snapshots/`)**
- Flatten the protobuf into columnar format using polars. One row per entity (trip update / vehicle position / alert), with the snapshot timestamp added as a column.
- Good for: large-scale analytics, efficient compression, direct use with polars/pandas/DuckDB.
- Two strategies:
  - **Single file with append**: buffer N snapshots in memory, append as a row group periodically. Risk: crash loses the buffer.
  - **Partitioned directory**: write one parquet file per snapshot (or per time window, e.g. hourly). Crash-safe — each file is complete. Use `polars.scan_parquet("snapshots/*.parquet")` to query across them lazily.
- Partitioned directory is the safer choice for long-running collection. Hourly rotation keeps file count manageable.

**Recommendation**: start with partitioned parquet directory — it's crash-safe, works naturally with polars, and avoids the JSON-in-SQLite compromise. A future `gtfs-cli explore` command can `scan_parquet` the directory for analysis.
