"""Unit tests for TripUpdateDiffEngine — pure logic, no I/O."""

from gtfs_cli.engines.trip_update import TripUpdateDiffEngine


def _make_entity(
    entity_id: str = "100",
    trip_id: str = "T1",
    route_id: str = "501",
    stop_ids: list[str] | None = None,
    timestamp: str | None = None,
) -> dict:
    """Helper to build a trip update entity dict."""
    entity: dict = {
        "id": entity_id,
        "trip_update": {
            "trip": {"trip_id": trip_id, "route_id": route_id},
            "stop_time_update": [
                {"stop_id": sid} for sid in (stop_ids or [])
            ],
        },
    }
    if timestamp is not None:
        entity["trip_update"]["timestamp"] = timestamp
    return entity


engine = TripUpdateDiffEngine()


def test_new_trip_emits_started():
    entity = _make_entity(trip_id="T1", route_id="503")
    events = engine.diff(None, entity)
    assert len(events) == 1
    assert "started" in events[0].description
    assert "T1" in events[0].description
    assert "503" in events[0].description


def test_removed_trip_emits_ended():
    entity = _make_entity(trip_id="T1", route_id="503")
    events = engine.diff(entity, None)
    assert len(events) == 1
    assert "ended" in events[0].description
    assert "T1" in events[0].description


def test_departed_stop_detected():
    before = _make_entity(stop_ids=["A", "B", "C"])
    after = _make_entity(stop_ids=["B", "C"])
    events = engine.diff(before, after)
    assert len(events) == 1
    assert "departed stop A" in events[0].description


def test_multiple_stops_departed():
    before = _make_entity(stop_ids=["A", "B", "C"])
    after = _make_entity(stop_ids=["C"])
    events = engine.diff(before, after)
    assert len(events) == 1
    assert "departed 2 stops" in events[0].description
    assert "A" in events[0].description
    assert "B" in events[0].description


def test_no_change_no_events():
    entity = _make_entity(stop_ids=["A", "B", "C"])
    events = engine.diff(entity, entity)
    assert events == []


def test_new_stop_added_no_event():
    before = _make_entity(stop_ids=["A", "B"])
    after = _make_entity(stop_ids=["A", "B", "C"])
    events = engine.diff(before, after)
    assert events == []


def test_entity_key_uses_trip_id():
    """Entity key should be trip_id, not entity id."""
    entity = {"id": "123", "trip_update": {"trip": {"trip_id": "T1"}}}
    assert engine.entity_key(entity) == "T1"


def test_entity_key_falls_back_to_entity_id():
    """When trip_id is missing, fall back to entity id."""
    entity = {"id": "123", "trip_update": {"trip": {}}}
    assert engine.entity_key(entity) == "123"


def test_trip_label_without_route():
    """When route_id is missing, label should just be the trip_id."""
    entity = {
        "id": "1",
        "trip_update": {
            "trip": {"trip_id": "T5"},
            "stop_time_update": [],
        },
    }
    events = engine.diff(None, entity)
    assert len(events) == 1
    assert "T5" in events[0].description
    assert "route" not in events[0].description


def test_started_with_departure_eta():
    """Trip started event should include next departure ETA when available."""
    entity = {
        "id": "1",
        "trip_update": {
            "trip": {"trip_id": "T1", "route_id": "501"},
            "timestamp": 1000,
            "stop_time_update": [
                {"stop_sequence": 1, "departure": {"time": 1090}, "stop_id": "A"},
                {"stop_sequence": 2, "arrival": {"time": 1200}, "stop_id": "B"},
            ],
        },
    }
    events = engine.diff(None, entity)
    assert len(events) == 1
    assert "started" in events[0].description
    assert "1m 30s" in events[0].description


def test_started_without_timestamp_no_eta():
    """No ETA when entity has no timestamp to compute against."""
    entity = _make_entity(stop_ids=["A", "B"])
    events = engine.diff(None, entity)
    assert len(events) == 1
    assert "started" in events[0].description
    assert "departure" not in events[0].description


def test_started_departure_in_past_shows_now():
    """If departure time is at or before entity timestamp, show 'now'."""
    entity = {
        "id": "1",
        "trip_update": {
            "trip": {"trip_id": "T1", "route_id": "501"},
            "timestamp": 1000,
            "stop_time_update": [
                {"stop_sequence": 1, "departure": {"time": 990}, "stop_id": "A"},
            ],
        },
    }
    events = engine.diff(None, entity)
    assert "now" in events[0].description


def test_bulk_departure_suppressed():
    """More than BULK_DEPARTURE_THRESHOLD stops disappearing at once is suppressed."""
    before = _make_entity(stop_ids=["A", "B", "C", "D", "E", "F", "G", "H"])
    after = _make_entity(stop_ids=["H"])
    events = engine.diff(before, after)
    assert events == []


def test_at_threshold_not_suppressed():
    """Exactly BULK_DEPARTURE_THRESHOLD departed stops should still emit."""
    from gtfs_cli.engines.trip_update import BULK_DEPARTURE_THRESHOLD

    # Create exactly threshold + 1 stops, keep 1 → departed == threshold
    stop_ids = [str(i) for i in range(BULK_DEPARTURE_THRESHOLD + 1)]
    before = _make_entity(stop_ids=stop_ids)
    after = _make_entity(stop_ids=[stop_ids[-1]])
    events = engine.diff(before, after)
    assert len(events) == 1
    assert "departed" in events[0].description


def test_over_threshold_suppressed():
    """More than BULK_DEPARTURE_THRESHOLD departed stops should be suppressed."""
    from gtfs_cli.engines.trip_update import BULK_DEPARTURE_THRESHOLD

    # Create threshold + 2 stops, keep 1 → departed == threshold + 1
    stop_ids = [str(i) for i in range(BULK_DEPARTURE_THRESHOLD + 2)]
    before = _make_entity(stop_ids=stop_ids)
    after = _make_entity(stop_ids=[stop_ids[-1]])
    events = engine.diff(before, after)
    assert events == []
