"""SQLAlchemy-backed implementations of services.protocols.*Repository.

Per docs/rebuild/02-TDD.md §1.2: every repo is constructed with a
session + user_id; user_id is appended to every WHERE clause as the
final mandatory filter (D6 / lesson 9.4).
"""

from app.infra.repositories.banned_entity_repo import BannedEntityRepo
from app.infra.repositories.conversation_repo import ConversationRepo
from app.infra.repositories.deprecation_repo import DeprecationRepo
from app.infra.repositories.episodic_repo import EpisodicRepo
from app.infra.repositories.event_repo import EventRepo
from app.infra.repositories.message_repo import MessageRepo
from app.infra.repositories.profile_repo import ProfileRepo
from app.infra.repositories.relationship_repo import RelationshipRepo
from app.infra.repositories.user_repo import UserRepo

__all__ = [
    "BannedEntityRepo",
    "ConversationRepo",
    "DeprecationRepo",
    "EpisodicRepo",
    "EventRepo",
    "MessageRepo",
    "ProfileRepo",
    "RelationshipRepo",
    "UserRepo",
]
