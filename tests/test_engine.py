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


def record_shots(engine) -> list:
    """Every flame pattern the engine starts, with its actual start time."""
    shots = []
    fire = engine._fire_shot

    def spy(shot, now):
        shots.append(shot)
        return fire(shot, now)

    engine._fire_shot = spy
    return shots


def test_armed_cycle_fires_flame_only_inside_pattern_pulses(engine):
    engine.gate.arm()
    shots = record_shots(engine)
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E + 12.0)

    safety_ch = engine.rig["flame"].channel("safety")
    fire_ch = engine.rig["flame"].channel("fire")
    L = FLAME_ARM_LEAD
    pulses = [w for s in shots for w in s.fire_windows(L)]
    arms = [(s.start, s.end(L)) for s in shots]
    edges = tuple(e for w in pulses + arms for e in w)
    assert {s.source for s in shots} >= {"dormant->imminent", "imminent", "imminent->eruption"}
    for t, frame in frames:
        if near_edge(t, edges):
            continue
        if any(a <= t < b for a, b in pulses):
            assert frame[fire_ch - 1] == 255, f"flame should burn at t={t:.2f}"
            assert frame[safety_ch - 1] == FLAME_SAFETY_ARM_VALUE
        elif any(a <= t < b for a, b in arms):
            assert frame[fire_ch - 1] == 0, f"fire between pulses at t={t:.2f}"
            assert frame[safety_ch - 1] == FLAME_SAFETY_ARM_VALUE
        else:
            assert frame[fire_ch - 1] == 0, f"flame leaked at t={t:.2f}"
            assert frame[safety_ch - 1] == 0, f"safety leaked at t={t:.2f}"


def test_flame_finale_clears_the_first_co2_and_eruption_is_flame_free(engine):
    engine.gate.arm()
    shots = record_shots(engine)
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    frames = run(engine, 0.0, E + 12.0)
    first_co2 = E + min(start for start, _ in engine.co2_windows)
    clear = engine.flame.config.clear_before_co2
    finale = [s for s in shots if s.source == "imminent->eruption"]
    assert len(finale) == 1
    assert finale[0].end(FLAME_ARM_LEAD) == pytest.approx(first_co2 - clear, abs=DT)
    assert all(s.end(FLAME_ARM_LEAD) <= first_co2 - clear + DT for s in shots)
    fire_ch = engine.rig["flame"].channel("fire")
    assert all(f[fire_ch - 1] == 0 for t, f in frames if t >= E)


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


def test_disarm_mid_pulse_kills_flame_next_frame(engine):
    engine.gate.arm()
    shots = record_shots(engine)
    engine.machine.request_eruption()
    engine.step(0.0)
    a, b = shots[0].fire_windows(FLAME_ARM_LEAD)[0]
    t = (a + b) / 2
    run(engine, DT, t)
    fire_ch = engine.rig["flame"].channel("fire")
    assert engine.driver.last_frame[fire_ch - 1] == 255
    engine.gate.disarm()
    engine.step(t + DT)
    assert engine.driver.last_frame[fire_ch - 1] == 0


def test_lights_flare_on_flame_pulses_even_disarmed(engine):
    shots = record_shots(engine)
    engine.machine.request_eruption()
    engine.step(0.0)
    a, b = shots[0].fire_windows(FLAME_ARM_LEAD)[0]
    run(engine, DT, (a + b) / 2)
    frame = engine.driver.last_frame
    for name in ("par1", "par6", "caldera_left"):
        f = engine.rig[name]
        assert frame[f.channel("dimmer") - 1] == 255
        assert frame[f.channel("red") - 1] == 255
        assert frame[f.channel("blue") - 1] == 0
    assert frame[engine.rig["flame"].channel("fire") - 1] == 0


def test_transition_patterns_follow_the_move_but_not_an_operator_hold(engine):
    engine.gate.arm()
    shots = record_shots(engine)
    t = engine.machine.dwell + DT
    run(engine, 0.0, t)  # dormant dwell expires: dormant -> active
    assert engine.machine.phase is Phase.ACTIVE
    assert [s.source for s in shots] == ["dormant->active"]
    engine.machine.hold = True  # active -> dormant by hold: no fizzle pop
    run(engine, t + DT, t + 5.0)
    assert engine.machine.phase is Phase.DORMANT
    assert [s.source for s in shots] == ["dormant->active"]


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
    which the breathing phases never do. Flame flares are off here: they
    light everything on each pulse and are tested on their own."""
    from dmx_volcano.flame import FlameConfig

    engine.flame.config = FlameConfig()
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
        {"t": 1.0, "action": "sound", "file": []},  # empty pool
        {"t": 1.0, "action": "sound", "file": ["a.wav", 3]},
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
