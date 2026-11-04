"""Tests du journal de consommation et du budget quotidien."""

from datetime import date, timedelta

from atlas.core.usage import UsageLog
from atlas.llm.base import Usage


def usage(tokens: int, cost: float | None = None) -> Usage:
    return Usage(total_tokens=tokens, cost=cost)


class Clock:
    def __init__(self) -> None:
        self.day = date(2026, 11, 4)

    def __call__(self) -> date:
        return self.day


def test_empty_log_is_not_exhausted() -> None:
    log = UsageLog(100, 1.0)

    assert log.tokens_today() == 0
    assert log.cost_today() == 0
    assert log.exhausted() is None
    assert log.turn_cost("absent") is None
    assert log.turn_tokens("absent") == 0


def test_totals_per_turn_and_per_day() -> None:
    log = UsageLog(1000, 1.0)
    log.record("t1", "m", usage(10, 0.01))
    log.record("t1", "m", usage(5, 0.02))
    log.record("t2", "m", usage(7))

    assert log.turn_tokens("t1") == 15
    assert log.turn_cost("t1") == 0.03
    assert log.turn_cost("t2") is None
    assert log.tokens_today() == 22
    assert log.cost_today() == 0.03


def test_token_budget_reached() -> None:
    log = UsageLog(20, 1.0)
    log.record("t", "m", usage(19))
    assert log.exhausted() is None

    log.record("t", "m", usage(1))

    assert "tokens" in (log.exhausted() or "")


def test_cost_budget_reached() -> None:
    log = UsageLog(10_000, 0.05)
    log.record("t", "m", usage(10, 0.05))

    assert "dollars" in (log.exhausted() or "")


def test_total_resets_the_next_day() -> None:
    clock = Clock()
    log = UsageLog(10, 1.0, today=clock)
    log.record("t", "m", usage(50))
    assert log.exhausted() is not None

    clock.day += timedelta(days=1)

    assert log.tokens_today() == 0
    assert log.exhausted() is None
    assert len(log.entries) == 1


def test_estimated_flag_is_kept() -> None:
    log = UsageLog(100, 1.0)

    log.record("t", "m", Usage(total_tokens=3, estimated=True))

    assert log.entries[0].estimated is True
