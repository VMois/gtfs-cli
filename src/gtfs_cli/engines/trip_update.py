from gtfs_cli.engines.base import DiffEngine, Event

# If more stops than this disappear in one diff, it's likely a bulk producer
# update (schedule correction, feed reset) rather than real-time movement.
BULK_DEPARTURE_THRESHOLD = 5


class TripUpdateDiffEngine(DiffEngine):
    """Detects trip started/ended and vehicle departed stop events.

    Uses the "shrinking list" approach for departure detection: when a stop_id
    disappears from the stop_time_update list between consecutive snapshots,
    the vehicle departed that stop. This matches TTC's feed behavior.
    """

    def entity_key(self, entity: dict) -> str:
        """Key by trip_id rather than entity id.

        TTC (and other producers) can replace an entity with a new entity id
        for the same trip_id. Keying by trip_id treats this as a single
        continuous trip instead of a false end+start.
        """
        trip_id = entity.get("trip_update", {}).get("trip", {}).get("trip_id")
        if trip_id:
            return trip_id
        return entity.get("id", "unknown")

    def diff(self, before: dict | None, after: dict | None) -> list[Event]:
        if before is None and after is not None:
            eid = self._entity_id(after)
            label = self._trip_label(after)
            desc = f"Trip {label} started [{eid}]"
            eta = self._next_departure_eta(after)
            if eta is not None:
                desc += f". Next departure in {eta}"
            return [
                Event(
                    timestamp=self._timestamp(after),
                    description=desc,
                )
            ]

        if after is None and before is not None:
            eid = self._entity_id(before)
            return [
                Event(
                    timestamp=self._timestamp(before),
                    description=f"Trip {self._trip_label(before)} ended [{eid}]",
                )
            ]

        if before is not None and after is not None:
            return self._diff_stops(before, after)

        return []

    def _diff_stops(self, before: dict, after: dict) -> list[Event]:
        before_stops = self._stop_ids(before)
        after_stops = self._stop_ids(after)
        departed = before_stops - after_stops

        if not departed:
            return []

        # Large bulk removals are likely producer-side schedule corrections,
        # not real-time movement — suppress to reduce noise.
        if len(departed) > BULK_DEPARTURE_THRESHOLD:
            return []

        eid = self._entity_id(after)
        label = self._trip_label(after)
        ts = self._timestamp(after)
        sorted_stops = sorted(departed)
        next_stop = self._next_stop(after)

        if len(sorted_stops) == 1:
            description = f"Trip {label} departed stop {sorted_stops[0]} [{eid}]"
        else:
            description = f"Trip {label} departed {len(sorted_stops)} stops ({', '.join(sorted_stops)}) [{eid}]"

        if next_stop:
            description += f" → next stop {next_stop}"

        return [Event(timestamp=ts, description=description)]

    @staticmethod
    def _next_stop(entity: dict) -> str | None:
        """Get the next stop_id (lowest stop_sequence) from stop_time_update."""
        stop_time_updates = entity.get("trip_update", {}).get("stop_time_update", [])
        if not stop_time_updates:
            return None
        first = min(stop_time_updates, key=lambda s: s.get("stop_sequence", 0))
        return first.get("stop_id")

    @staticmethod
    def _entity_id(entity: dict) -> str:
        """Extract entity id for debug display."""
        return entity.get("id", "?")

    @staticmethod
    def _stop_ids(entity: dict) -> set[str]:
        """Extract set of stop_ids from the stop_time_update list."""
        trip_update = entity.get("trip_update", {})
        stop_time_updates = trip_update.get("stop_time_update", [])
        return {stu["stop_id"] for stu in stop_time_updates if "stop_id" in stu}

    @staticmethod
    def _trip_label(entity: dict) -> str:
        """Build a human-readable label like '27823020 (route 503)'."""
        trip = entity.get("trip_update", {}).get("trip", {})
        trip_id = trip.get("trip_id", "unknown")
        route_id = trip.get("route_id")
        if route_id:
            return f"{trip_id} (route {route_id})"
        return trip_id

    @staticmethod
    def _next_departure_eta(entity: dict) -> str | None:
        """Get a human-readable ETA for the next departure.

        Finds the first stop (lowest stop_sequence) that has a departure time,
        computes how far in the future it is relative to the entity timestamp.
        """
        trip_update = entity.get("trip_update", {})
        entity_ts = trip_update.get("timestamp")
        if entity_ts is None:
            return None

        stop_time_updates = trip_update.get("stop_time_update", [])
        if not stop_time_updates:
            return None

        # Find the first stop with a departure time (lowest stop_sequence)
        first = min(stop_time_updates, key=lambda s: s.get("stop_sequence", 0))
        dep_time = first.get("departure", {}).get("time")
        if dep_time is None:
            # Fall back to arrival time
            dep_time = first.get("arrival", {}).get("time")
        if dep_time is None:
            return None

        diff_seconds = int(dep_time) - int(entity_ts)
        if diff_seconds <= 0:
            return "now"

        minutes = diff_seconds // 60
        seconds = diff_seconds % 60
        if minutes > 0:
            return f"{minutes}m {seconds}s"
        return f"{seconds}s"

    @staticmethod
    def _timestamp(entity: dict) -> str:
        """Extract timestamp from entity, falling back to empty string."""
        ts = entity.get("trip_update", {}).get("timestamp")
        if ts is not None:
            return str(ts)
        return ""
