# `gtfs-cli static` Command Design

## Purpose

Work with static GTFS schedule data (the ZIP of CSV files that describes routes, stops, trips, and schedules). Unlike GTFS-RT which requires protobuf parsing, static GTFS files are plain CSVs — but cross-file referential integrity, spec validation, and multi-table joins are where a dedicated tool adds value over generic tools like `unzip` + `csvkit`.

## Scope for this document

Initial implementation: `gtfs-cli static info`

ZIP support and further subcommands (`validate`, `cross-check`) are out of scope for now.

---

## `gtfs-cli static info [FOLDER]`

### Purpose

Print a high-level summary of a static GTFS feed for someone inspecting an unfamiliar feed:
is it current, what agency, what city, what modes of transport, how large.

### Usage

```
gtfs-cli static info [FOLDER]
```

`FOLDER` defaults to the current directory. This lets users do:

```bash
# Unzip a feed and inspect it
curl https://.../feed.zip -o feed.zip
unzip feed.zip -d my_feed/
gtfs-cli static info my_feed/

# Or if already in the unzipped folder
gtfs-cli static info
```

### Output

```
Agency:   Toronto Transit Commission
Timezone: America/Toronto
Version:  20240501
Valid:    2024-05-01 to 2024-08-31

Stops     8,421
Routes      153
Trips    48,230

Route types
──────────────────── ──────
Bus                    142
Subway / Metro           4
Tram / Light Rail        7

Service days: Mon Tue Wed Thu Fri Sat Sun
Optional files: shapes.txt, feed_info.txt
```

### What each piece tells the user

- **Agency + timezone** — confirms which operator and city this feed covers
- **Version + valid dates** — immediately shows whether the feed is current or stale; this is the first thing anyone checks
- **Stops / Routes / Trips** — quick size sanity check; a feed with 3 routes but 50,000 trips signals something odd
- **Route type breakdown** — shows what modes are included (bus-only? multimodal?)
- **Service days** — which days of the week have any scheduled service; derived from `calendar.txt` by OR-ing all active service rows
- **Optional files** — tells the user what extra data is available (shapes for mapping, frequencies for headway-based feeds)

### Data sources

| Output field   | Source file       | Notes                                          |
|----------------|-------------------|------------------------------------------------|
| Agency, timezone | `agency.txt`    | First row; most feeds have one agency          |
| Version        | `feed_info.txt`   | Optional file; omitted if absent               |
| Valid dates    | `feed_info.txt`   | Fallback: min/max of `start_date`/`end_date` in `calendar.txt` |
| Stop count     | `stops.txt`       | Row count                                      |
| Route count    | `routes.txt`      | Row count                                      |
| Route types    | `routes.txt`      | `route_type` grouped count                     |
| Trip count     | `trips.txt`       | Row count                                      |
| Service days   | `calendar.txt`    | Any service on that weekday across all rows    |

`stop_times.txt` is not read — its row count is not meaningful for a summary.

### Missing files

Missing required files print a warning to stderr. The command still runs and summarises whatever is present. Exit code 0 unless the folder does not exist.

---

## Implementation plan

### Files to create

- `src/gtfs_cli/commands/static.py` — `static_app` Typer sub-app with `info` command

### Files to modify

- `src/gtfs_cli/main.py` — register `static_app` with `app.add_typer(static_app)`

### Files to create (tests)

- `tests/test_static_info.py` — uses `tmp_path` to create minimal CSV fixtures, no network

### Key implementation notes

1. **Typer sub-app** — `static_app = typer.Typer(name="static")`, registered in `main.py` with `app.add_typer(static_app)`. Commands hang off `static_app` as `@static_app.command("info")`.

2. **Polars for all data processing** — add `polars` to `pyproject.toml` dependencies. Use `pl.read_csv()` for all file reads. Row counts via `.height`, groupby via `.group_by().len()`, column access via `.get_column()`.

3. **BOM handling** — many GTFS producers include a UTF-8 BOM. Polars `read_csv` handles this with `encoding="utf8-lossy"` or by passing `eol_char` — verify behaviour and add `infer_schema_length=0` to read all columns as strings (GTFS IDs like `route_type` look numeric but should be treated as strings for mapping).

4. **Date formatting** — GTFS dates are `YYYYMMDD` strings. Display as `YYYY-MM-DD` for readability.

5. **Route type mapping** — GTFS route_type values 0–7, 11, 12. Unknown values displayed as `Type N`.

6. **Service days** — load `calendar.txt` with polars, then check if any row has a `1` in each weekday column (`monday`…`sunday`) using `.select(pl.col("monday").cast(pl.Int8).max())` style aggregation.

7. **Rich output** — use `rich.table.Table` with `box.SIMPLE`. Errors/warnings via `print(..., file=sys.stderr)`.

---

## Future subcommands (not in scope now)

- `gtfs-cli static validate [FOLDER]` — referential integrity, required fields, sequence ordering
- `gtfs-cli static cross-check FOLDER RT_FEED` — compare static trips/stops against a live GTFS-RT feed
- ZIP source support for all subcommands
