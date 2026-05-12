from pathlib import Path

from typer.testing import CliRunner

from gtfs_cli.main import app

runner = CliRunner()

FIXTURES_DIR = Path(__file__).parent / "fixtures"
TTC_STATIC = FIXTURES_DIR / "ttc_static"


def test_static_info_ttc_shows_agency():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert result.exit_code == 0
    assert "TTC" in result.output


def test_static_info_ttc_shows_timezone():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert "America/Toronto" in result.output


def test_static_info_ttc_shows_valid_dates():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert "2026-" in result.output


def test_static_info_ttc_shows_counts():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert "Stops" in result.output
    assert "Routes" in result.output
    assert "Trips" in result.output


def test_static_info_ttc_shows_route_types():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert "Bus" in result.output
    assert "Subway / Metro" in result.output


def test_static_info_ttc_shows_service_days():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert "Mon" in result.output
    assert "Sat" in result.output
    assert "Sun" in result.output


def test_static_info_ttc_shows_optional_files():
    result = runner.invoke(app, ["static", "info", str(TTC_STATIC)])
    assert "shapes" in result.output
    assert "Optional" in result.output


def test_static_info_default_folder_is_cwd(tmp_path):
    """When no folder argument is given, current directory is used."""
    (tmp_path / "agency.txt").write_text("agency_name,agency_url,agency_timezone\nTest Agency,http://test.com,UTC\n")
    result = runner.invoke(app, ["static", "info", str(tmp_path)])
    assert result.exit_code == 0
    assert "Test Agency" in result.output


def test_static_info_missing_folder_exits_with_error():
    result = runner.invoke(app, ["static", "info", "/nonexistent/path"])
    assert result.exit_code == 1


def test_static_info_partial_feed_exits_ok(tmp_path):
    """A folder with only some files still exits 0 and shows what it can."""
    (tmp_path / "agency.txt").write_text("agency_name,agency_url,agency_timezone\nTest Agency,http://test.com,UTC\n")
    result = runner.invoke(app, ["static", "info", str(tmp_path)])
    assert result.exit_code == 0
    assert "Test Agency" in result.output


def test_static_info_uses_feed_info_dates(tmp_path):
    """feed_info.txt dates take priority over calendar.txt dates."""
    (tmp_path / "feed_info.txt").write_text(
        "feed_publisher_name,feed_publisher_url,feed_lang,feed_start_date,feed_end_date,feed_version\n"
        "Test,http://test.com,en,20250101,20251231,v42\n"
    )
    result = runner.invoke(app, ["static", "info", str(tmp_path)])
    assert "2025-01-01" in result.output
    assert "2025-12-31" in result.output
    assert "v42" in result.output


def test_static_info_falls_back_to_calendar_dates(tmp_path):
    """When feed_info.txt is absent, valid dates come from calendar.txt."""
    (tmp_path / "calendar.txt").write_text(
        "service_id,monday,tuesday,wednesday,thursday,friday,saturday,sunday,start_date,end_date\n"
        "WD,1,1,1,1,1,0,0,20240601,20240831\n"
        "WE,0,0,0,0,0,1,1,20240601,20240831\n"
    )
    result = runner.invoke(app, ["static", "info", str(tmp_path)])
    assert "2024-06-01" in result.output
    assert "2024-08-31" in result.output
