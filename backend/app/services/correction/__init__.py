"""Online memory-correction pipeline.

Per docs/rebuild/06-Subsystem-Correction.md.

Responsibilities:
- ``extractor``: pull correction targets from the user's correction utterance
- ``searcher``: span-find candidate memories that contain the target ref
- ``judge``: LLM-judges each candidate (action + confidence)
- ``banned_extractor``: LLM-extract entities to ban
- ``applier``: cross-layer soft-delete + banned insert + audit log
"""

from app.services.correction.applier import (
    ApplyOutcome,
    CorrectionApplier,
)
from app.services.correction.banned_extractor import (
    BANNED_EXTRACTOR_SYSTEM,
    BannedEntityResult,
    BannedExtractor,
    clean_banned_entity,
)
from app.services.correction.extractor import (
    CORRECTION_EXTRACTOR_SYSTEM,
    CorrectionExtractionResult,
    CorrectionTargetExtractor,
)
from app.services.correction.judge import (
    CORRECTION_JUDGE_SYSTEM,
    CorrectionJudge,
    JudgementOutcome,
)
from app.services.correction.searcher import (
    CandidateBundle,
    CorrectionCandidateSearcher,
)

__all__ = [
    "ApplyOutcome",
    "BANNED_EXTRACTOR_SYSTEM",
    "BannedEntityResult",
    "BannedExtractor",
    "CORRECTION_EXTRACTOR_SYSTEM",
    "CORRECTION_JUDGE_SYSTEM",
    "CandidateBundle",
    "CorrectionApplier",
    "CorrectionCandidateSearcher",
    "CorrectionExtractionResult",
    "CorrectionJudge",
    "CorrectionTargetExtractor",
    "JudgementOutcome",
    "clean_banned_entity",
]
