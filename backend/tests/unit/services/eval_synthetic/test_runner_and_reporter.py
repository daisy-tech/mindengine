"""Tests for the synthetic runner + reporter."""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from app.domain.eval import EvalCase, ExpectedAssertions, HistoryMsg
from app.domain.route import Intent, Personality
from app.services.eval_synthetic.reporter import build_report
from app.services.eval_synthetic.runner import (
    SyntheticTurnOutcome,
    check_case,
    confusion_matrix,
    run_cases,
)


def _case(
    *,
    case_id: str = "c1",
    personality: Personality = Personality.BALANCED,
    intents=("casual",),
    must_activate=(),
    must_contain=(),
    forbidden_in_reply=(),
    history=(),
):
    return EvalCase(
        id=case_id,
        personality=personality,
        history=[HistoryMsg(role=h["role"], content=h["content"]) for h in history],
        user="你好",
        expected=ExpectedAssertions(
            intents=[Intent(i) for i in intents],
            must_activate_keywords=list(must_activate),
            must_contain=list(must_contain),
            forbidden_phrases_in_reply=list(forbidden_in_reply),
        ),
        tags=[],
    )


def _outcome(**kw) -> SyntheticTurnOutcome:
    defaults: dict = {"reply": "你也好。", "intent": "casual"}
    defaults.update(kw)
    return SyntheticTurnOutcome(**defaults)


def test_check_case_all_passes_yields_pass():
    case = _case()
    out = check_case(case, _outcome())
    assert out.passed is True
    assert all(c.passed for c in out.checks)


def test_check_case_intent_mismatch_fails():
    case = _case(intents=("memory_challenge",))
    out = check_case(case, _outcome(intent="casual"))
    intent_check = next(c for c in out.checks if c.name == "intent")
    assert intent_check.passed is False


def test_check_case_must_activate_uses_keywords_or_system():
    case = _case(must_activate=("怀化",))
    out = check_case(
        case,
        _outcome(activated_keywords=("怀化", "李娜")),
    )
    assert out.passed
    out2 = check_case(case, _outcome(system_excerpt="老家在怀化"))
    assert out2.passed
    out3 = check_case(case, _outcome())
    assert not out3.passed


def test_check_case_must_contain_in_reply():
    case = _case(must_contain=("怀宁",))
    assert check_case(case, _outcome(reply="是怀宁。")).passed
    assert not check_case(case, _outcome(reply="是怀化。")).passed


def test_check_case_forbidden_in_reply_fails():
    case = _case(forbidden_in_reply=("已记录",))
    out = check_case(case, _outcome(reply="已记录，我会注意。"))
    assert not out.passed
    forbid = next(c for c in out.checks if c.name == "forbidden_in_reply")
    assert "已记录" in forbid.detail


def test_check_case_records_runner_error_as_failed_check():
    case = _case()
    out = check_case(case, _outcome(error="runner_error: x"))
    assert not out.passed


@dataclass
class _ScriptedRunner:
    outcomes: list[SyntheticTurnOutcome]
    idx: int = 0

    async def run(self, case: EvalCase) -> SyntheticTurnOutcome:
        oc = self.outcomes[self.idx]
        self.idx += 1
        _ = case
        return oc


@pytest.mark.asyncio
async def test_run_cases_collects_results_in_order():
    cases = [_case(case_id="a"), _case(case_id="b")]
    runner = _ScriptedRunner(
        outcomes=[
            _outcome(reply="a-ok"),
            _outcome(reply="b-ok"),
        ]
    )
    batch = await run_cases(runner, cases)
    assert [it.case.id for it in batch.items] == ["a", "b"]
    assert batch.finished_at >= batch.started_at


@pytest.mark.asyncio
async def test_run_cases_does_not_abort_on_runner_exception():
    @dataclass
    class _BoomRunner:
        async def run(self, case: EvalCase) -> SyntheticTurnOutcome:
            _ = case
            raise RuntimeError("boom")

    cases = [_case()]
    batch = await run_cases(_BoomRunner(), cases)
    assert len(batch.items) == 1
    assert batch.items[0].outcome.error is not None


def test_confusion_matrix_counts_actuals_per_expected():
    cases = [_case(case_id="x", intents=("memory_challenge",))]
    runner = _ScriptedRunner(outcomes=[_outcome(intent="casual")])
    import asyncio

    batch = asyncio.run(run_cases(runner, cases))
    cm = confusion_matrix(batch.items)
    assert cm["memory_challenge"]["casual"] == 1


@pytest.mark.asyncio
async def test_build_report_aggregates_pass_rate_and_cm():
    cases = [
        _case(case_id="ok", intents=("casual",)),
        _case(case_id="bad", intents=("memory_challenge",)),
    ]
    runner = _ScriptedRunner(
        outcomes=[
            _outcome(intent="casual"),
            _outcome(intent="casual"),
        ]
    )
    batch = await run_cases(runner, cases)
    report = build_report(batch, run_id="r1", run_type="smoke")
    assert report["total"] == 2
    assert report["passed"] == 1
    assert report["pass_rate"] == 0.5
    assert "memory_challenge" in report["intent_confusion_matrix"]
