"""Flame patterns: parsing, config validation, and the imminent planner."""

import random
import tomllib
from pathlib import Path

import pytest

from dmx_volcano.flame import FlameConfig, FlamePlanner, Timing, length, parse_pattern

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"
LEAD = 0.5


def show_flame() -> FlameConfig:
    return FlameConfig.from_config(tomllib.loads(CONFIG.read_text())["flame"])


def planner(cfg: FlameConfig, first_co2: float = 1.0, seed: int = 0) -> FlamePlanner:
    return FlamePlanner(cfg, LEAD, 30.0, first_co2, random.Random(seed))


def test_pattern_parses_dots_dashes_and_ignores_spaces():
    timing = Timing(dot=0.1, dash=0.3, gap=0.2)
    assert parse_pattern("- . . -", timing) == parse_pattern("-..-", timing)
    assert parse_pattern("-..-", timing) == pytest.approx(
        [(0.0, 0.3), (0.5, 0.1), (0.8, 0.1), (1.1, 0.3)]
    )
    assert length(parse_pattern("-..-", timing)) == pytest.approx(1.4)


@pytest.mark.parametrize("pattern", ["", "  ", ".x-", "SOS"])
def test_bad_patterns_are_refused(pattern):
    with pytest.raises(ValueError):
        parse_pattern(pattern, Timing())


def test_dot_below_the_gate_floor_is_refused_at_load():
    with pytest.raises(ValueError, match="outside"):
        FlameConfig.from_config({"dot": 0.05, "imminent": {"patterns": ["."]}})


@pytest.mark.parametrize("key", ["eruption->dormant", "dormant->eruption", "active"])
def test_moves_after_co2_or_impossible_are_refused(key):
    with pytest.raises(ValueError):
        FlameConfig.from_config({"transitions": {key: ["."]}})


def test_finale_that_cannot_clear_the_first_co2_is_refused():
    cfg = FlameConfig.from_config({"transitions": {"imminent->eruption": ["------"]}})
    cfg.check_finale(LEAD, 30.0, first_co2=1.0)
    with pytest.raises(ValueError, match="finale"):
        cfg.check_finale(LEAD, 3.0, first_co2=1.0)


@pytest.mark.parametrize("seed", range(20))
def test_imminent_plan_never_overlaps_and_finale_ends_clear_of_co2(seed):
    cfg = show_flame()
    shots = planner(cfg, seed=seed).plan("active", "imminent", "timer", 100.0)
    assert shots[0].source == "active->imminent"
    assert shots[-1].source == "imminent->eruption"
    assert {s.source for s in shots[1:-1]} == {"imminent"}
    for a, b in zip(shots, shots[1:]):
        assert a.end(LEAD) + cfg.rest[0] <= b.start + 1e-9
    # imminent ends at 130; first CO2 at eruption t=1.0 -> flame done by 129.5.
    assert shots[-1].end(LEAD) == pytest.approx(130.0 + 1.0 - cfg.clear_before_co2)


def test_hold_and_eruption_entry_plan_no_flame():
    p = planner(show_flame())
    assert p.plan("active", "dormant", "hold", 0.0) == []
    assert p.plan("imminent", "eruption", "timer", 0.0) == []
    assert [s.source for s in p.plan("active", "dormant", "fizzle", 0.0)] == ["active->dormant"]


def test_picks_never_repeat_back_to_back():
    p = planner(show_flame())
    picks = [p.plan("dormant", "active", "timer", 0.0)[0].pattern for _ in range(30)]
    assert all(a != b for a, b in zip(picks, picks[1:]))
