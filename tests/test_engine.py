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


def test_armed_eruption_fires_flame_only_in_cue_windows(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E + 21.0)

    safety_ch = engine.rig["flame"].channel("safety")
    fire_ch = engine.rig["flame"].channel("fire")
    co2_ch = engine.rig["co2"].channel("jet")

    # Cue timeline: flame bursts at t=5 and t=9 (0.8s), co2 at t=4 (1.5s),
    # all relative to eruption start at t=E. A flame cue opens the safety
    # channel at its t and raises fire FLAME_ARM_LEAD later, so fire burns
    # over [t+lead, t+lead+0.8) while safety holds over [t, t+lead+0.8).
    L = FLAME_ARM_LEAD
    fire_windows = ((E + 5.0 + L, E + 5.8 + L), (E + 9.0 + L, E + 9.8 + L))
    arm_windows = ((E + 5.0, E + 5.8 + L), (E + 9.0, E + 9.8 + L))
    flame_edges = tuple(e for w in fire_windows + arm_windows for e in w)
    co2_edges = (E + 4.0, E + 5.5)
    for t, frame in frames:
        fire = frame[fire_ch - 1]
        if not near_edge(t, flame_edges):
            if any(a <= t < b for a, b in fire_windows):
                assert fire == 255, f"flame should burn at t={t:.2f}"
                assert frame[safety_ch - 1] == FLAME_SAFETY_ARM_VALUE
            elif any(a <= t < b for a, b in arm_windows):
                # the lead: safety open, fire still clamped
                assert fire == 0, f"fire rose before the arm lead at t={t:.2f}"
                assert frame[safety_ch - 1] == FLAME_SAFETY_ARM_VALUE
            else:
                assert fire == 0, f"flame leaked at t={t:.2f}"
                assert frame[safety_ch - 1] == 0
        if not near_edge(t, co2_edges) and not (E + 4.0 <= t < E + 5.5):
            assert frame[co2_ch - 1] == 0, f"co2 leaked at t={t:.2f}"

    assert any(f[co2_ch - 1] == 255 for t, f in frames if E + 4.0 <= t < E + 5.5)


def test_disarmed_eruption_never_puts_pyro_on_the_wire(engine):
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E + 21.0)
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
    E = engine.machine.config.imminent_duration
    t = E + 5.0 + FLAME_ARM_LEAD + 0.2  # into the first flame burst's fire window
    run(engine, 0.0, t)
    fire_ch = engine.rig["flame"].channel("fire")
    assert engine.driver.last_frame[fire_ch - 1] == 255
    engine.gate.disarm()
    engine.step(t + DT)
    assert engine.driver.last_frame[fire_ch - 1] == 0


def test_eruption_count_cooldown_and_co2_usage(engine):
    engine.gate.arm()
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    run(engine, 0.0, E + 25.0)  # imminent + eruption (20s) ends into cooldown
    assert engine.state.eruption_count == 1
    # The 1.5s CO2 cue burned duration * rate.
    assert engine.co2.used_kg == pytest.approx(1.5 * engine.co2.rate_kg_per_s)
    engine.machine.request_eruption()  # inside 60s cooldown -> ignored
    run(engine, E + 25.0, E + 30.0)
    assert engine.state.eruption_count == 1


def test_co2_alert_marks_once_when_threshold_crossed(engine):
    engine.gate.arm()
    # Shrink the tank so one 1.5s cue crosses the warn threshold.
    engine.co2.capacity_kg = 1.5 * engine.co2.rate_kg_per_s * 1.1
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    run(engine, 0.0, E + 6.0)
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
        CONFIG.read_text().replace("duration = 0.8", "duration = 10.5", 1)
    )
    with pytest.raises(ValueError, match="duration must be"):
        build_engine(cfg, FakeDriver())


@pytest.mark.parametrize(
    "cue",
    [
        {"t": 1.0, "action": "flame"},  # unknown action
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
