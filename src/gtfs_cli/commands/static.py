import sys
from datetime import date
from pathlib import Path

import polars as pl
import typer
from rich import box
from rich.console import Console
from rich.table import Table

static_app = typer.Typer(name="static", help="Work with static GTFS data.")
console = Console()

ROUTE_TYPE_NAMES = {
    "0": "Tram / Light Rail",
    "1": "Subway / Metro",
    "2": "Rail",
    "3": "Bus",
    "4": "Ferry",
    "5": "Cable Tram",
    "6": "Aerial Lift / Gondola",
    "7": "Funicular",
    "11": "Trolleybus",
    "12": "Monorail",
}

REQUIRED_FILES = ["agency.txt", "stops.txt", "routes.txt", "trips.txt", "stop_times.txt", "calendar.txt"]
OPTIONAL_FILES = [
    "shapes.txt", "calendar_dates.txt", "feed_info.txt",
    "frequencies.txt", "transfers.txt", "fare_attributes.txt", "fare_rules.txt",
]


def _read_csv(path: Path) -> pl.DataFrame:
    # infer_schema_length=0 keeps all columns as strings — GTFS IDs like route_type
    # look numeric but must stay as strings for name lookups
    return pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy")


def _kv() -> Table:
    """Right-aligned dim label beside a plain value — no borders, no header."""
    t = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
    t.add_column(justify="right", style="dim", no_wrap=True)
    t.add_column()
    return t


def _section(label: str) -> None:
    console.print(f"\n[bold]{label}[/bold]")


def _format_date(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


def _parse_date(yyyymmdd: str) -> date:
    return date(int(yyyymmdd[:4]), int(yyyymmdd[4:6]), int(yyyymmdd[6:]))


def _gtfs_time(t: str | None) -> str:
    """Format a GTFS time string. Times past midnight use a +1 suffix (e.g. 26:30 → 02:30+1)."""
    if not t:
        return ""
    parts = t.split(":")
    h, m = int(parts[0]), int(parts[1])
    if h >= 24:
        return f"{h - 24:02d}:{m:02d}+1"
    return f"{h:02d}:{m:02d}"


def _time_range(times: pl.Series) -> tuple[str, str]:
    """Return (first, last) GTFS time strings, sorted by numeric value not lexicographically.

    Needed because GTFS hours are not always zero-padded ('5:30:00' vs '10:00:00'),
    so string min/max gives wrong results.
    """
    mins = times.map_elements(
        lambda t: int(t.split(":")[0]) * 60 + int(t.split(":")[1]),
        return_dtype=pl.Int32,
    )
    return times[mins.arg_min()], times[mins.arg_max()]


def _human_size(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.0f} MB"
    if n >= 1_000:
        return f"{n / 1_000:.0f} KB"
    return f"{n} B"


@static_app.command("info")
def static_info(
    folder: Path = typer.Argument(
        default=Path("."),
        help="Folder containing GTFS static .txt files. Defaults to current directory.",
    ),
) -> None:
    """Show high-level info about a static GTFS feed.

    Examples:

        gtfs-cli static info

        gtfs-cli static info my_gtfs_folder
    """
    if not folder.is_dir():
        print(f"Error: '{folder}' is not a directory.", file=sys.stderr)
        raise typer.Exit(code=1)

    def load(name: str) -> pl.DataFrame | None:
        p = folder / name
        return _read_csv(p) if p.exists() else None

    agency_df     = load("agency.txt")
    stops_df      = load("stops.txt")
    routes_df     = load("routes.txt")
    trips_df      = load("trips.txt")
    stop_times_df = load("stop_times.txt")
    calendar_df   = load("calendar.txt")
    shapes_df     = load("shapes.txt")
    feed_info_df  = load("feed_info.txt")

    # ── Agency ────────────────────────────────────────────────────────────────
    agency_name = agency_timezone = agency_lang = None
    if agency_df is not None and not agency_df.is_empty():
        row = agency_df.row(0, named=True)
        name = row.get("agency_name", "")
        aid  = row.get("agency_id", "")
        # Show "Full Name (ID)" only when the ID is a meaningful short code, not a bare integer
        if aid and aid != name and not aid.isdigit():
            agency_name = f"{name} ({aid})"
        else:
            agency_name = name
        agency_timezone = row.get("agency_timezone")
        agency_lang     = row.get("agency_lang")

    # ── Dates ─────────────────────────────────────────────────────────────────
    feed_version = feed_start = feed_end = None
    if feed_info_df is not None and not feed_info_df.is_empty():
        row = feed_info_df.row(0, named=True)
        feed_version = row.get("feed_version")
        feed_start   = row.get("feed_start_date")
        feed_end     = row.get("feed_end_date")

    if not (feed_start and feed_end) and calendar_df is not None:
        if "start_date" in calendar_df.columns and "end_date" in calendar_df.columns:
            feed_start = calendar_df["start_date"].min()
            feed_end   = calendar_df["end_date"].max()

    valid_str = age_str = None
    if feed_start and feed_end:
        start_d    = _parse_date(feed_start)
        end_d      = _parse_date(feed_end)
        total_days = (end_d - start_d).days
        valid_str  = f"{_format_date(feed_start)} → {_format_date(feed_end)}  ({total_days} days)"

        today     = date.today()
        days_old  = (today - start_d).days
        days_left = (end_d - today).days
        if days_left > 0:
            age_str = f"{days_old} days · expires in {days_left} days"
        else:
            age_str = f"{days_old} days · [red]expired {abs(days_left)} days ago[/red]"

    # ── Shapes ────────────────────────────────────────────────────────────────
    shapes_str = None
    if shapes_df is not None and "shape_id" in shapes_df.columns:
        shapes_str = f"{shapes_df.height:,} points across {shapes_df['shape_id'].n_unique():,} shapes"

    # ── Service windows ───────────────────────────────────────────────────────
    service_windows: list[tuple[str, str]] = []
    if calendar_df is not None and trips_df is not None and stop_times_df is not None:
        trip_days = trips_df.select(["trip_id", "service_id"]).join(
            calendar_df.select(["service_id", "monday", "saturday", "sunday"]),
            on="service_id",
            how="left",
        )
        times = stop_times_df.select(["trip_id", "departure_time"]).join(
            trip_days, on="trip_id", how="left"
        )
        for label, col in [("Weekday", "monday"), ("Saturday", "saturday"), ("Sunday", "sunday")]:
            if col not in times.columns:
                continue
            day = times.filter(
                (pl.col(col) == "1")
                & pl.col("departure_time").is_not_null()
                & (pl.col("departure_time") != "")
            )["departure_time"]
            if day.is_empty():
                continue
            first, last = _time_range(day)
            service_windows.append((label, f"{_gtfs_time(first)} – {_gtfs_time(last)}"))

    # ── Service days ──────────────────────────────────────────────────────────
    active_days: list[str] = []
    if calendar_df is not None:
        for col, abbr in [("monday","Mon"),("tuesday","Tue"),("wednesday","Wed"),
                          ("thursday","Thu"),("friday","Fri"),("saturday","Sat"),("sunday","Sun")]:
            if col in calendar_df.columns and calendar_df[col].cast(pl.Int8).max() == 1:
                active_days.append(abbr)

    # ── Route types ───────────────────────────────────────────────────────────
    route_types: list[tuple[str, int]] = []
    if routes_df is not None and "route_type" in routes_df.columns:
        for row in (
            routes_df.group_by("route_type").len().sort("len", descending=True).iter_rows(named=True)
        ):
            route_types.append((
                ROUTE_TYPE_NAMES.get(row["route_type"], f"Type {row['route_type']}"),
                row["len"],
            ))

    # ── Row counts for largest-files section ──────────────────────────────────
    known_counts: dict[str, int] = {}
    for fname, df in [
        ("stops.txt", stops_df), ("routes.txt", routes_df), ("trips.txt", trips_df),
        ("stop_times.txt", stop_times_df), ("shapes.txt", shapes_df),
    ]:
        if df is not None:
            known_counts[fname] = df.height

    # ══ OUTPUT ════════════════════════════════════════════════════════════════

    meta = _kv()
    if agency_name:     meta.add_row("Agency",    agency_name)
    if agency_timezone: meta.add_row("Timezone",  agency_timezone)
    if agency_lang:     meta.add_row("Language",  agency_lang)
    if feed_version:    meta.add_row("Version",   feed_version)
    if valid_str:       meta.add_row("Valid",      valid_str)
    if age_str:         meta.add_row("Feed age",  age_str)
    console.print(meta)

    counts = _kv()
    if stops_df is not None:      counts.add_row("Stops",      f"{stops_df.height:,}")
    if routes_df is not None:     counts.add_row("Routes",     f"{routes_df.height:,}")
    if trips_df is not None:      counts.add_row("Trips",      f"{trips_df.height:,}")
    if stop_times_df is not None: counts.add_row("Stop times", f"{stop_times_df.height:,}")
    if shapes_str:                counts.add_row("Shapes",     shapes_str)
    console.print()
    console.print(counts)

    if route_types:
        _section("Route types")
        t = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
        t.add_column()
        t.add_column(justify="right")
        for name, count in route_types:
            t.add_row(f"  {name}", f"{count:,}")
        console.print(t)

    if service_windows:
        _section("Service windows")
        t = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
        t.add_column(style="dim", no_wrap=True)
        t.add_column()
        for label, window in service_windows:
            t.add_row(f"  {label}", window)
        console.print(t)

    if active_days:
        _section("Service days")
        console.print("  " + "  ".join(active_days))

    present_required = [f for f in REQUIRED_FILES if (folder / f).exists()]
    present_optional = [f for f in OPTIONAL_FILES if (folder / f).exists()]
    missing_files    = [f for f in REQUIRED_FILES + OPTIONAL_FILES if not (folder / f).exists()]
    if present_required or present_optional or missing_files:
        _section("Files")
        t = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
        t.add_column(style="dim", no_wrap=True)
        t.add_column()
        def _names(files: list[str]) -> str:
            return "  ".join(f.removesuffix(".txt") for f in files)
        if present_required: t.add_row("  Required", _names(present_required))
        if present_optional: t.add_row("  Optional", _names(present_optional))
        if missing_files:    t.add_row("  Missing",  f"[dim]{_names(missing_files)}[/dim]")
        console.print(t)

    txt_files = sorted(folder.glob("*.txt"), key=lambda p: p.stat().st_size, reverse=True)
    if txt_files:
        _section("Largest files")
        t = Table(box=None, show_header=False, padding=(0, 1), pad_edge=False)
        t.add_column(no_wrap=True)
        t.add_column(justify="right", style="dim")
        t.add_column(justify="right", style="dim")
        for p in txt_files[:3]:
            rows = known_counts.get(p.name)
            row_str = f"{rows:,} rows" if rows is not None else ""
            t.add_row(f"  {p.name}", _human_size(p.stat().st_size), row_str)
        console.print(t)

    console.print()
