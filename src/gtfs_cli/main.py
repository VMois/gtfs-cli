import typer

from gtfs_cli.commands.fetch import fetch

app = typer.Typer(
    name="gtfs-cli",
    help="CLI tool to fetch, archive, process and explore GTFS-RT data.",
    no_args_is_help=True,
)


@app.callback()
def callback() -> None:
    """CLI tool to fetch, archive, process and explore GTFS-RT data."""


app.command()(fetch)
