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
from app.services.eval_synthetic.report_store import (
    StoredReportSummary,
    delete_report,
    list_reports,
    load_report,
    save_report,
)
from app.services.eval_synthetic.reporter import build_report
from app.services.eval_synthetic.runner import (
    SyntheticRunResult,
    check_case,
)
from app.services.eval_synthetic.runner_chat import ChatBackedSyntheticRunner

__all__ = [
    "CaseLoadError",
    "ChatBackedSyntheticRunner",
    "DEFAULT_CASE_FILES",
    "SeedSummary",
    "StoredReportSummary",
    "SyntheticRunResult",
    "build_report",
    "check_case",
    "delete_report",
    "list_case_files",
    "list_reports",
    "load_cases",
    "load_report",
    "save_report",
    "seed_eval_persona",
]
