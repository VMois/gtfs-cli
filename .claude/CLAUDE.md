This project is a CLI tool to fetch, archive, process and explore GTFS data, in particulat GTFS-RT (Real-Time).

## Implementation stack

Must use the following libraries:

- Typer for CLI framework, Rich for nice display
- gtfs-realtime-bindings for protobuf parsing
- requests with connection pooling
- polars for transformations
- pyarrow for parquet output
- uv for package and env management

## Online resources allowed to fetch

You are allowed to fetch from the following domains:

https://gtfs.org/documentation/realtime/reference - GTFS RT specification
https://typer.tiangolo.com/tutorial/ - Typer CLI docs/tutorial

## GTSF-RT TTC Toronto feed we are using for testing

Base URL: `https://gtfsrt.ttc.ca`

### Available Feeds

**Service Alerts**
- Combined: `/alerts/all?format=binary` (all alerts: subway, bus, streetcar, accessiblity, stops)

**Trip Updates**
- Trip Updates: `/trips/update?format=binary`
- Modified Trip Updates: `/trips/modified_update?format=binary`

**Vehicle Positions**
- Vehicle Positions: `/vehicles/position?format=binary`

### Testing & Development

**Primary test feeds:**
- Trip updates: `https://gtfsrt.ttc.ca/trips/update?format=binary`
- Vehicle positions: `https://gtfsrt.ttc.ca/vehicles/position?format=binary`
- All alerts: `https://gtfsrt.ttc.ca/alerts/all?format=binary`

**Feed format:** Binary protobuf (use `format=text` for human-readable textproto during debugging)

**Sample fetch command for testing:**
```bash
curl https://gtfsrt.ttc.ca/trips/update?format=binary -o test_trip_updates.pb
```

## Guideline on build CLI

- Include usage examples in --help
- CLI interfaces are an API - version appropriately with semantic versioning

## Project structure

Use console scripts in pyproject.toml

gtfs-cli/
├── pyproject.toml          # uv managed
├── src/
│   └── gtfs_cli/
│       ├── __main__.py     # Entry point
│       ├── main.py         # Typer app + version
│       ├── commands/
│       │   ├── __init__.py
└── tests/
│   └── fixtures/
│   └── test_abc123.py

