"""End-to-end: cues -> safety gate -> wire bytes, over simulated time.

These tests step the real ShowEngine at 40Hz through full eruptions and
assert on the exact frames the driver would transmit. Manual triggers pass
through the fixed 30s IMMINENT warning phase, so eruption cue times are
offset by E (imminent_duration) below.
"""

from pathlib import Path

import pytest

from dmx_volcano.driver.fake import FakeDriver
from dmx_volcano.phases import Phase
from dmx_volcano.show import FLAME_ARM_LEAD, FLAME_SAFETY_ARM_VALUE, build_engine

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"
DT = 1 / 40


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # EventLog and CO2 state write under cwd
    monkeypatch.setattr("dmx_volcano.audio.play", lambda p: None)  # keep tests silent
    eng, state, _ = build_engine(CONFIG, FakeDriver())
    eng.gate.enabled_by_config = True  # show-night config: pyro_enabled=true
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


def test_manual_trigger_passes_through_imminent(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    run(engine, 0.0, E - 0.5)
    assert engine.machine.phase is Phase.IMMINENT
    run(engine, E - 0.5 + DT, E + 1.0)
    assert engine.machine.phase is Phase.ERUPTION


def test_eruption_fires_co2_only_in_cue_windows(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E + 12.0)
    co2_ch = engine.rig["co2"].channel("jet")
    windows = [(E + a, E + b) for a, b in engine.co2_windows]
    edges = tuple(e for w in windows for e in w)
    for t, frame in frames:
        if near_edge(t, edges):
            continue
        expect = 255 if any(a <= t < b for a, b in windows) else 0
        assert frame[co2_ch - 1] == expect, f"co2 wrong at t={t:.2f}"


def test_disarmed_eruption_never_puts_pyro_on_the_wire(engine):
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E + 12.0)
    pyro = engine.rig.pyro_channels()
    for t, frame in frames:
        for ch in pyro:
            assert frame[ch - 1] == 0, f"pyro ch{ch} leaked at t={t:.2f} while disarmed"
    # The show itself still runs: fog and pars are alive during the eruption.
    fog_ch = engine.rig["fogger"].channel("fog")
    assert any(f[fog_ch - 1] == 255 for _, f in frames)


def test_eruption_count_cooldown_and_co2_usage(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    run(engine, 0.0, E + 12.0)  # imminent + eruption (10s) ends into cooldown
    assert engine.state.eruption_count == 1
    # Both CO2 cues burned duration * rate.
    total = sum(b - a for a, b in engine.co2_windows)
    assert engine.co2.used_kg == pytest.approx(total * engine.co2.rate_kg_per_s)
    engine.machine.request_eruption()  # inside 60s cooldown -> ignored
    run(engine, E + 12.0, E + 17.0)
    assert engine.state.eruption_count == 1


def test_co2_alert_marks_once_when_threshold_crossed(engine):
    engine.gate.arm()
    # Shrink the tank so the first CO2 cue crosses the warn threshold.
    start, end = engine.co2_windows[0]
    engine.co2.capacity_kg = (end - start) * engine.co2.rate_kg_per_s * 1.1
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    run(engine, 0.0, E + end + 0.5)
    assert engine.co2.alerted is True
    assert engine.co2.remaining_fraction <= engine.co2.warn_remaining_fraction


def test_imminent_look_is_distinct_from_active(engine):
    """During imminent the pars must hit both full-off and full-on (heartbeat),
    which the breathing phases never do."""
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E - 0.5)
    dim_ch = engine.rig["par1"].channel("dimmer")
    values = [f[dim_ch - 1] for _, f in frames]
    assert min(values) <= 5, "heartbeat should have dark gaps"
    assert max(values) >= 240, "heartbeat should peak near full"
    # Breathing phases (dormant look: dimmer 60-100) never leave that band.
    assert sum(1 for v in values if v < 30) > len(values) * 0.3


def test_overlong_pyro_cue_refused_at_startup_even_in_rehearsal(tmp_path, monkeypatch):
    # The gate only checks burst length once armed, so without startup
    # validation this config would pass every rehearsal and crash armed.
    monkeypatch.chdir(tmp_path)
    cfg = tmp_path / "show.toml"
    cfg.write_text(
        CONFIG.read_text().replace("duration = 2.0", "duration = 4.5", 1)
    )
    with pytest.raises(ValueError, match="duration must be"):
        build_engine(cfg, FakeDriver())


@pytest.mark.parametrize(
    "cue",
    [
        {"t": 1.0, "action": "flame"},  # unknown action
        {"t": 1.0, "action": "flame_burst", "duration": 0.5},  # eruption is CO2 only
        {"t": 1.0, "action": "co2_burst"},  # missing duration
        {"t": 1.0, "action": "fog", "level": 300},
        {"t": -1.0, "action": "pars"},
    ],
)
def test_malformed_cues_are_refused(cue):
    from dmx_volcano.cues import load_timeline, validate_timeline

    with pytest.raises(ValueError):
        validate_timeline(load_timeline([cue]))


def test_shutdown_still_disarms_when_the_driver_is_dead(engine):
    class DeadDriver(FakeDriver):
        def send_frame(self, universe):
            raise OSError("USB gone")

        def close(self):
            raise OSError("USB gone")

    engine.gate.arm()
    engine.driver = DeadDriver()
    engine.shutdown(0.0)  # must not raise
    assert not engine.gate.pyro_enabled


def test_shutdown_blacks_out_the_wire(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    t = 0.0
    while engine.machine.phase is not Phase.ERUPTION:
        t += DT
        engine.step(t)
    engine.shutdown(t)
    assert engine.driver.last_frame == bytes(512)
