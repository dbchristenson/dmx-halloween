"""Lighting looks: per-light motion, dominant vs. accent colors, cascading
heartbeat, independent strobes, the phase-change hit, and the caldera's
smoke colors and CO2 white blasts."""

import tomllib
from pathlib import Path

import pytest

from dmx_volcano.driver.universe import Universe
from dmx_volcano.fixtures import load_rig
from dmx_volcano.lighting import FIREWORKS, build_looks, render
from dmx_volcano.phases import Phase

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"
RIG = load_rig(CONFIG)
PARS = [RIG[f"par{n}"] for n in range(1, 7)]
CRATER = [RIG["caldera_left"], RIG["caldera_right"]]
FOGGER = RIG["fogger"]


@pytest.fixture
def looks():
    looks = build_looks(tomllib.loads(CONFIG.read_text()))
    looks["imminent"]["duration"] = 30.0
    return looks


def frames(looks, phase, t0, t1, fps=20):
    """Per frame: [(dimmer, (r, g, b)) for each outer par]. `since` runs
    from t0, so t0 >= 2 skips the phase-change hit."""
    u = Universe()
    out = []
    for k in range(round((t1 - t0) * fps)):
        t = t0 + k / fps
        render(phase, RIG, u, t, t, looks)
        out.append([
            (u[f.channel("dimmer")], tuple(u[f.channel(c)] for c in ("red", "green", "blue")))
            for f in PARS
        ])
    return out


@pytest.mark.parametrize(
    "phase, dominant, accent_seen",
    [
        (Phase.DORMANT, (255, 0, 0), lambda rgb: rgb[2] > 120),  # purple
        (Phase.ACTIVE, (255, 80, 0), lambda rgb: rgb[1] > 200 and rgb[0] < 60),  # green
    ],
)
def test_breathing_phases_hold_a_dominant_color_with_a_minority_of_accents(
    looks, phase, dominant, accent_seen
):
    run = frames(looks, phase, 2.0, 600.0)
    accented_at_once = max(sum(rgb != dominant for _, rgb in frame) for frame in run)
    assert accented_at_once <= 3, "accents must stay a minority of the six pars"
    on_dominant = sum(rgb == dominant for frame in run for _, rgb in frame)
    assert on_dominant > 0.55 * len(run) * len(PARS)
    assert any(accent_seen(rgb) for frame in run for _, rgb in frame)


@pytest.mark.parametrize("phase", [Phase.DORMANT, Phase.ACTIVE])
def test_breathing_pars_move_independently_through_deep_lows_and_full_highs(looks, phase):
    run = frames(looks, phase, 2.0, 120.0)
    dimmers = [d for frame in run for d, _ in frame]
    assert min(dimmers) <= 25 and max(dimmers) >= 250
    spread = [max(d for d, _ in frame) - min(d for d, _ in frame) for frame in run]
    assert sum(s > 60 for s in spread) > 0.5 * len(run), "pars should not breathe in unison"


def test_imminent_beats_cascade_across_the_pars_in_mixed_colors(looks):
    run = frames(looks, Phase.IMMINENT, 2.0, 29.0, fps=40)
    # A beat reaches the pars at different moments: often some are lit
    # while others are dark.
    split = sum(any(d > 100 for d, _ in f) and any(d == 0 for d, _ in f) for f in run)
    assert split > 0.2 * len(run)
    lit = {rgb for frame in run for d, rgb in frame if d > 100}
    assert (255, 255, 255) in lit and (255, 0, 0) in lit and (255, 90, 0) in lit
    assert max(d for frame in run for d, _ in frame) >= 240


def test_eruption_strobes_are_independent_and_vary_in_brightness(looks):
    run = frames(looks, Phase.ERUPTION, 2.0, 18.0, fps=40)
    mixed = sum(len({d > 0 for d, _ in frame}) == 2 for frame in run)
    assert mixed > 0.5 * len(run), "pars should not strobe in unison"
    peaks = {d for frame in run for d, _ in frame if d > 0}
    assert min(peaks) < 100 and max(peaks) > 240
    assert {rgb for frame in run for _, rgb in frame} == {(255, 60, 0), (255, 0, 0)}


@pytest.mark.parametrize("phase", list(Phase))
def test_every_phase_change_snaps_dark_then_sweeps_a_full_flash_par_by_par(looks, phase):
    u = Universe()
    gap, step = looks["transition"]["gap"], looks["transition"]["step"]

    def dimmers(since):
        render(phase, RIG, u, 100.0 + since, since, looks)
        return [u[f.channel("dimmer")] for f in PARS]

    assert dimmers(gap / 2) == [0] * 6
    for j in range(6):  # par j flashes to full while the pars after it wait dark
        at_fire = dimmers(gap + j * step + 0.001)
        assert at_fire[j] >= 250
        assert at_fire[j + 1:] == [0] * (5 - j)


def crater(u):
    """[(dimmer, (r, g, b))] for each crater light, then the fogger LEDs."""
    out = [
        (u[f.channel("dimmer")], tuple(u[f.channel(c)] for c in ("red", "green", "blue")))
        for f in CRATER
    ]
    return out + [(None, tuple(u[FOGGER.channel(c)] for c in ("red", "green", "blue")))]


def test_dormant_crater_glows_low_sulfur_blue(looks):
    u = Universe()
    seen = []
    for k in range(6000):
        t = 2 + k / 10
        render(Phase.DORMANT, RIG, u, t, t, looks)
        seen.append(crater(u))
    assert max(d for frame in seen for d, _ in frame[:2]) <= 110
    on_blue = sum(rgb == (0, 60, 255) for frame in seen for _, rgb in frame[:2])
    assert on_blue > 0.5 * 2 * len(seen)


def test_active_smoke_cycles_firework_colors_and_is_never_one_color(looks):
    u = Universe()
    colors, same = set(), 0
    for k in range(1200):
        t = 2 + k / 10
        render(Phase.ACTIVE, RIG, u, t, t, looks)
        frame = crater(u)
        colors |= {rgb for _, rgb in frame[:2]}
        same += len({rgb for _, rgb in frame}) == 1  # crater lights and fogger match
    pure = {tuple(c) for c in FIREWORKS}
    assert pure <= colors, "each light should walk the whole palette"
    assert same < 0.02 * 1200


def test_eruption_crater_is_white_pops_then_solid_white_through_each_co2_cue(looks):
    u = Universe()
    windows = [(4.0, 5.5), (10.0, 11.0)]

    def at(since):
        render(Phase.ERUPTION, RIG, u, 50.0 + since, since, looks, windows)
        return crater(u)

    for since in (4.0, 5.0, 10.5):
        for dimmer, rgb in at(since):
            assert rgb == (255, 255, 255) and dimmer in (255, None)
    between = [at(7.0 + k / 40)[:2] for k in range(80)]
    assert {rgb for frame in between for _, rgb in frame} == {(255, 255, 255)}
    dimmers = [d for frame in between for d, _ in frame]
    assert min(dimmers) < 50 and max(dimmers) > 200, "white pops between blasts"
    split = sum(abs(a[0] - b[0]) > 60 for a, b in between)
    assert split > 0.3 * len(between), "the two crater lights pop independently"


def test_fogger_leds_scale_color_since_they_have_no_dimmer(looks):
    u = Universe()
    render(Phase.ERUPTION, RIG, u, 104.0, 4.0, looks, [(4.0, 5.0)])
    assert [u[FOGGER.channel(c)] for c in ("red", "green", "blue")] == [255, 255, 255]
    assert [u[FOGGER.channel(c)] for c in ("strobe", "auto", "auto_speed")] == [0, 0, 0]


def test_the_crater_hits_after_the_par_sweep(looks):
    u = Universe()
    gap, step = looks["transition"]["gap"], looks["transition"]["step"]
    render(Phase.ACTIVE, RIG, u, 100.0, gap + 5 * step + 0.001, looks)
    assert all(d == 0 for d, _ in crater(u)[:2])
    render(Phase.ACTIVE, RIG, u, 100.0, gap + 6 * step + 0.001, looks)
    assert all(d >= 250 for d, _ in crater(u)[:2])
