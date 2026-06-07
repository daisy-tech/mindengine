"""Synthetic evaluation: hand-written cases scored against a real chat run.

Per docs/rebuild/07-Subsystem-Eval-Lab.md §2.

Public surface:
- ``case_loader`` — load smoke / full case JSON files
- ``runner`` — run a single case through ChatOrchestrator
- ``reporter`` — aggregate run results into the §2.4 report
- ``persona_seed`` — deterministically seed an eval-only user with
  baseline memory state (used by the API ``seed_persona``).
"""

from app.services.eval_synthetic.case_loader import (
    DEFAULT_CASE_FILES,
    CaseLoadError,
    list_case_files,
    load_cases,
)
from app.services.eval_synthetic.persona_seed import (
    SeedSummary,
    seed_eval_persona,
)
from app.services.eval_synthetic.reporter import build_report
from app.services.eval_synthetic.runner import (
    SyntheticRunResult,
    check_case,
)

__all__ = [
    "CaseLoadError",
    "DEFAULT_CASE_FILES",
    "SeedSummary",
    "SyntheticRunResult",
    "build_report",
    "check_case",
    "list_case_files",
    "load_cases",
    "seed_eval_persona",
]
