"""Flame patterns: parsing, config validation, and the imminent planner."""

import random
import tomllib
from pathlib import Path

import pytest

from dmx_volcano.flame import FlameConfig, FlamePlanner, Timing, length, parse_pattern
from dmx_volcano.safety import FLAME_AFTER_CO2

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"
LEAD = 0.5


def show_flame() -> FlameConfig:
    return FlameConfig.from_config(tomllib.loads(CONFIG.read_text())["flame"])


def planner(cfg: FlameConfig, first_co2: float = 1.0, seed: int = 0) -> FlamePlanner:
    return FlamePlanner(cfg, LEAD, 30.0, first_co2, random.Random(seed))


def test_pattern_parses_dots_dashes_and_ignores_spaces():
    timing = Timing(dot=0.15, dash=0.45, gap=0.25)
    assert parse_pattern("- . . -", timing) == parse_pattern("-..-", timing)
    assert parse_pattern("-..-", timing) == pytest.approx(
        [(0.0, 0.45), (0.7, 0.15), (1.1, 0.15), (1.5, 0.45)]
    )
    assert length(parse_pattern("-..-", timing)) == pytest.approx(1.95)


@pytest.mark.parametrize("pattern", ["", "  ", ".x-", "SOS"])
def test_bad_patterns_are_refused(pattern):
    with pytest.raises(ValueError):
        parse_pattern(pattern, Timing())


def test_dot_below_the_gate_floor_is_refused_at_load():
    with pytest.raises(ValueError, match="outside"):
        FlameConfig.from_config({"dot": 0.1, "imminent": {"patterns": ["."]}})


def test_gap_too_tight_to_ignite_is_refused_at_load():
    with pytest.raises(ValueError, match="fail to ignite"):
        FlameConfig.from_config({"gap": 0.0, "imminent": {"patterns": ["..."]}})
    FlameConfig.from_config({"gap": 0.0, "imminent": {"patterns": ["-"]}})  # one pulse: no gap


@pytest.mark.parametrize("key", ["dormant->eruption", "eruption->imminent", "active"])
def test_impossible_moves_are_refused(key):
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


def test_aftermath_waits_out_the_co2_lockout():
    cfg = show_flame()
    shots = planner(cfg).plan("eruption", "dormant", "timer", 112.0, co2_end=111.5)
    assert [s.source for s in shots] == ["eruption->dormant"]
    assert shots[0].fire_windows(LEAD)[0][0] == pytest.approx(111.5 + FLAME_AFTER_CO2)
    # CO2 long gone (e.g. never fired): the aftermath starts with dormant.
    shots = planner(cfg).plan("eruption", "dormant", "timer", 112.0)
    assert shots[0].start == 112.0


def test_hold_and_eruption_entry_plan_no_flame():
    p = planner(show_flame())
    assert p.plan("active", "dormant", "hold", 0.0) == []
    assert p.plan("imminent", "eruption", "timer", 0.0) == []
    assert [s.source for s in p.plan("active", "dormant", "fizzle", 0.0)] == ["active->dormant"]


def test_picks_never_repeat_back_to_back():
    p = planner(show_flame())
    picks = [p.plan("dormant", "active", "timer", 0.0)[0].pattern for _ in range(30)]
    assert all(a != b for a, b in zip(picks, picks[1:]))
