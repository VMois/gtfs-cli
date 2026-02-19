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

## POTENTIAL FEATURES
- `--format binary|table` output options
- `--output` file writing
- TTY auto-detection for output format (JSON for terminal, length-delimited protobuf for pipes)
- `--watch <seconds>` with length-delimited protobuf streaming
- Flush-after-write, graceful SIGINT handling
