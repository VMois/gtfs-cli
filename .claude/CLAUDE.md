This project is a CLI tool to fetch, archive, process and explore GTFS data, in particular GTFS-RT (Real-Time).

## Tech stack

- Typer for CLI framework, Rich for nice display
- gtfs-realtime-bindings for protobuf parsing
- httpx Python library for HTTP connection
- polars Python library for transformations
- pyarrow for parquet output
- uv for Python package and env management
- pytest for unit tests

## Online resources allowed to fetch

You are allowed to fetch from the following domains (full links provided as a helper):

https://gtfs.org/documentation/realtime/reference - GTFS RT specification
https://gtfs.org/documentation/realtime/language-bindings/python/ - GTFS Python bindings docs
https://typer.tiangolo.com/tutorial/ - Typer CLI docs/tutorial
https://docs.astral.sh/uv/ - uv Python package manager
https://docs.pola.rs/api/python - for polars docs
https://www.python-httpx.org/ - HTTPX docs

## TTC Toronto static GTFS feed

**Download URL:**
```
https://ckan0.cf.opendata.inter.prod-toronto.ca/dataset/7795b45e-e65a-4465-81fc-c36b9dfff169/resource/cfb6b2b8-6191-41e3-bda1-b175c51148cb/download/TTC%20Routes%20and%20Schedules%20Data.zip
```

**Sample workflow:**
```bash
curl -L "<url above>" -o /tmp/ttc_static.zip
unzip /tmp/ttc_static.zip -d /tmp/ttc_static/
gtfs-cli static info /tmp/ttc_static/
```

Fixtures in `tests/fixtures/ttc_static/` are sampled from this feed (trips, shapes, stop_times truncated to 300 rows).

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
- Write unit tests whenever you can; make sure not to use network when they are run
- When adding or changing a feature, update README.md examples and docs if they are affected

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
├── tests/
│   └── fixtures/
│   └── test_abc123.py
├── docs/  # contains description for different features for implementation
