from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass
class Event:
    """A human-readable event detected by comparing two snapshots."""

    timestamp: str
    description: str


class DiffEngine(ABC):
    """Abstract base for feed-type-specific diff engines.

    Each GTFS-RT feed type (trip updates, vehicle positions, alerts) has its own
    entity structure and its own notion of "meaningful change." Subclasses encode
    that domain knowledge while the view command handles I/O and display.
    """

    @abstractmethod
    def entity_key(self, entity: dict) -> str:
        """Extract a stable identity key from an entity."""

    @abstractmethod
    def diff(self, before: dict | None, after: dict | None) -> list[Event]:
        """Compare two versions of the same entity, return human-readable events.

        before=None means the entity is new.
        after=None means it was removed.
        """
