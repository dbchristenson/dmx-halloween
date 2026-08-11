"""End-to-end: cues -> safety gate -> wire bytes, over simulated time.

These tests step the real ShowEngine at 40Hz through full eruptions and
assert on the exact frames the driver would transmit.
"""

from pathlib import Path

import pytest

from dmx_volcano.driver.fake import FakeDriver
from dmx_volcano.show import FLAME_SAFETY_ARM_VALUE, build_engine

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"
DT = 1 / 40


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # EventLog writes logs/ under cwd
    eng, state, _ = build_engine(CONFIG, FakeDriver())
    eng.machine.start(0.0)
    return eng


def run(engine, t0: float, t1: float) -> list[tuple[float, bytes]]:
    frames = []
    for i in range(round(t0 / DT), round(t1 / DT) + 1):
        t = i * DT
        frames.append((t, engine.step(t)))
    return frames


def near_edge(t: float, edges: tuple[float, ...]) -> bool:
    """Within two ticks of a cue-window boundary — timing there depends on
    frame quantization, so assertions skip those frames."""
    return any(abs(t - e) < 2 * DT for e in edges)


def fire_values(engine, frames):
    ch = engine.rig["flame"].channel("fire")
    return [(t, f[ch - 1]) for t, f in frames]


def test_armed_eruption_fires_flame_only_in_cue_windows(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    frames = run(engine, 0.0, 21.0)

    safety_ch = engine.rig["flame"].channel("safety")
    fire_ch = engine.rig["flame"].channel("fire")
    co2_ch = engine.rig["co2"].channel("jet")

    # Cue timeline: flame bursts at t=5 and t=9 (0.8s), co2 at t=4 (1.5s).
    flame_edges = (5.0, 5.8, 9.0, 9.8)
    co2_edges = (4.0, 5.5)
    for t, frame in frames:
        fire = frame[fire_ch - 1]
        if not near_edge(t, flame_edges):
            in_window = (5.0 <= t < 5.8) or (9.0 <= t < 9.8)
            if in_window:
                assert fire == 255, f"flame should burn at t={t:.2f}"
                assert frame[safety_ch - 1] == FLAME_SAFETY_ARM_VALUE
            else:
                assert fire == 0, f"flame leaked at t={t:.2f}"
                assert frame[safety_ch - 1] == 0
        if not near_edge(t, co2_edges) and not (4.0 <= t < 5.5):
            assert frame[co2_ch - 1] == 0, f"co2 leaked at t={t:.2f}"

    assert any(f[co2_ch - 1] == 255 for t, f in frames if 4.0 <= t < 5.5)


def test_disarmed_eruption_never_puts_pyro_on_the_wire(engine):
    engine.machine.request_eruption()
    frames = run(engine, 0.0, 21.0)
    pyro = engine.rig.pyro_channels()
    for t, frame in frames:
        for ch in pyro:
            assert frame[ch - 1] == 0, f"pyro ch{ch} leaked at t={t:.2f} while disarmed"
    # The show itself still runs: fog and pars are alive during the eruption.
    fog_ch = engine.rig["fogger"].channel("fog")
    assert any(f[fog_ch - 1] == 255 for _, f in frames)


def test_disarm_mid_burst_kills_flame_next_frame(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    run(engine, 0.0, 5.2)  # into the first flame burst
    fire_ch = engine.rig["flame"].channel("fire")
    assert engine.driver.last_frame[fire_ch - 1] == 255
    engine.gate.disarm()
    engine.step(5.25)
    assert engine.driver.last_frame[fire_ch - 1] == 0


def test_eruption_count_and_cooldown(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    run(engine, 0.0, 25.0)  # eruption (20s) ends into cooldown
    assert engine.state.eruption_count == 1
    engine.machine.request_eruption()  # inside 60s cooldown -> ignored
    run(engine, 25.0, 30.0)
    assert engine.state.eruption_count == 1
