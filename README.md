# gtfs-cli

CLI tool to fetch, archive, process and explore GTFS-RT data.

## Installation

```bash
uv tool install gtfs-cli
```

To install from a local checkout:

```bash
uv tool install .
```

After installation, the `gtfs-cli` command is available globally.

## Development

```bash
uv sync
uv run gtfs-cli fetch "https://gtfsrt.ttc.ca/trips/update?format=binary"
```

Run tests:

```bash
uv run pytest tests/ -v
```
