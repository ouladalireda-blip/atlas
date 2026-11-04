"""Tests de la compétence clock."""

from datetime import datetime, timedelta

from atlas.skills.base import call
from atlas.skills.clock.skill import PARIS, ClockSkill


def test_now_is_read_only_and_takes_no_argument() -> None:
    (spec,) = ClockSkill().tools()

    assert spec.name == "clock_now"
    assert spec.effect is None
    assert spec.parameters["properties"] == {}


def test_now_returns_current_paris_time() -> None:
    (spec,) = ClockSkill().tools()

    result = datetime.fromisoformat(call(spec, {}))

    expected = datetime.now(PARIS)
    assert result.utcoffset() == expected.utcoffset()
    assert abs(expected - result) < timedelta(seconds=5)
