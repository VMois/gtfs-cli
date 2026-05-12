import sys
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

OPTIONAL_FILES = ["shapes.txt", "feed_info.txt", "frequencies.txt", "transfers.txt"]
WEEKDAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]


def _read_csv(path: Path) -> pl.DataFrame:
    # infer_schema_length=0 reads all columns as strings — important for GTFS because
    # fields like route_type look numeric but must stay as strings for name lookups
    return pl.read_csv(path, infer_schema_length=0, encoding="utf8-lossy")


def _format_date(yyyymmdd: str) -> str:
    if len(yyyymmdd) == 8:
        return f"{yyyymmdd[:4]}-{yyyymmdd[4:6]}-{yyyymmdd[6:]}"
    return yyyymmdd


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

    # Agency
    agency_name = None
    agency_timezone = None
    if (folder / "agency.txt").exists():
        df = _read_csv(folder / "agency.txt")
        if not df.is_empty():
            row = df.row(0, named=True)
            agency_name = row.get("agency_name")
            agency_timezone = row.get("agency_timezone")

    # Feed version and valid dates from feed_info.txt
    feed_version = None
    feed_start = None
    feed_end = None
    if (folder / "feed_info.txt").exists():
        df = _read_csv(folder / "feed_info.txt")
        if not df.is_empty():
            row = df.row(0, named=True)
            feed_version = row.get("feed_version")
            feed_start = row.get("feed_start_date")
            feed_end = row.get("feed_end_date")

    # Fallback: derive date range from calendar.txt
    if not (feed_start and feed_end) and (folder / "calendar.txt").exists():
        df = _read_csv(folder / "calendar.txt")
        if not df.is_empty() and "start_date" in df.columns and "end_date" in df.columns:
            feed_start = df["start_date"].min()
            feed_end = df["end_date"].max()

    # Service days from calendar.txt
    active_days: list[str] = []
    if (folder / "calendar.txt").exists():
        df = _read_csv(folder / "calendar.txt")
        for day in WEEKDAYS:
            if day in df.columns and df[day].cast(pl.Int8).max() == 1:
                active_days.append(day[:3].capitalize())

    # Counts
    counts: list[tuple[str, int]] = []
    for filename, label in [
        ("stops.txt", "Stops"),
        ("routes.txt", "Routes"),
        ("trips.txt", "Trips"),
    ]:
        if (folder / filename).exists():
            counts.append((label, _read_csv(folder / filename).height))

    # Route type breakdown
    route_type_counts: list[tuple[str, int]] = []
    if (folder / "routes.txt").exists():
        df = _read_csv(folder / "routes.txt")
        if "route_type" in df.columns:
            breakdown = (
                df.group_by("route_type")
                .len()
                .sort("len", descending=True)
            )
            for row in breakdown.iter_rows(named=True):
                name = ROUTE_TYPE_NAMES.get(row["route_type"], f"Type {row['route_type']}")
                route_type_counts.append((name, row["len"]))

    # --- Output ---
    if agency_name:
        console.print(f"[bold]Agency:[/bold]   {agency_name}")
    if agency_timezone:
        console.print(f"[bold]Timezone:[/bold] {agency_timezone}")
    if feed_version:
        console.print(f"[bold]Version:[/bold]  {feed_version}")
    if feed_start and feed_end:
        console.print(f"[bold]Valid:[/bold]    {_format_date(feed_start)} to {_format_date(feed_end)}")

    if counts:
        console.print()
        t = Table(box=box.SIMPLE, show_header=False)
        t.add_column("Label")
        t.add_column("Count", justify="right")
        for label, count in counts:
            t.add_row(label, f"{count:,}")
        console.print(t)

    if route_type_counts:
        console.print("[bold]Route types[/bold]")
        t = Table(box=box.SIMPLE, show_header=False)
        t.add_column("Type")
        t.add_column("Count", justify="right")
        for name, count in route_type_counts:
            t.add_row(name, f"{count:,}")
        console.print(t)

    if active_days:
        console.print(f"\n[bold]Service days:[/bold] {' '.join(active_days)}")

    optional_present = [f for f in OPTIONAL_FILES if (folder / f).exists()]
    if optional_present:
        console.print(f"[dim]Optional files: {', '.join(optional_present)}[/dim]")
