"""Memory extraction services — used by Celery workers in M3.

Split into one module per layer so a single worker maps 1:1 to a service:
- ``profile_merger``: pure merge logic for Profile (no IO)
- ``profile_extractor``: LLM-driven extraction → partial Profile
- ``event_extractor``: LLM extraction → list[Event]
- ``episodic_extractor``: LLM extraction → list[str] (facts)
- ``relationship_extractor``: LLM extraction → list[Relationship]

Each extractor accepts an ``LLMClient`` and is unit-tested against a
``MockLLMClient``.
"""

from app.services.memory_extract.dispatch_targets import (
    ALL_LAYERS,
    EXTRACT_TARGETS_BY_INTENT,
    ExtractLayer,
    targets_for,
)
from app.services.memory_extract.episodic_extractor import (
    EPISODIC_EXTRACTOR_SYSTEM,
    EpisodicExtractionResult,
    EpisodicExtractor,
)
from app.services.memory_extract.event_extractor import (
    EVENT_EXTRACTOR_SYSTEM,
    EventCandidate,
    EventExtractionResult,
    EventExtractor,
)
from app.services.memory_extract.profile_extractor import (
    PROFILE_EXTRACTOR_SYSTEM,
    ProfileExtractionResult,
    ProfileExtractor,
)
from app.services.memory_extract.profile_merger import (
    ProfileMerger,
    merge_profile,
)
from app.services.memory_extract.relationship_extractor import (
    RELATIONSHIP_EXTRACTOR_SYSTEM,
    RelationshipCandidate,
    RelationshipExtractionResult,
    RelationshipExtractor,
)

__all__ = [
    "ALL_LAYERS",
    "EPISODIC_EXTRACTOR_SYSTEM",
    "EVENT_EXTRACTOR_SYSTEM",
    "EXTRACT_TARGETS_BY_INTENT",
    "EpisodicExtractionResult",
    "EpisodicExtractor",
    "EventCandidate",
    "EventExtractionResult",
    "EventExtractor",
    "ExtractLayer",
    "PROFILE_EXTRACTOR_SYSTEM",
    "ProfileExtractionResult",
    "ProfileExtractor",
    "ProfileMerger",
    "RELATIONSHIP_EXTRACTOR_SYSTEM",
    "RelationshipCandidate",
    "RelationshipExtractionResult",
    "RelationshipExtractor",
    "merge_profile",
    "targets_for",
]
