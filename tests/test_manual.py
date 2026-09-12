"""Operator overrides: hold, pause, and the manual fixture-test console.

Same style as test_engine: step the real ShowEngine over simulated time and
assert on the exact bytes that would hit the wire.
"""

from pathlib import Path

import pytest

from dmx_volcano.driver.fake import FakeDriver
from dmx_volcano.manual import TestConsole
from dmx_volcano.phases import Phase
from dmx_volcano.show import FLAME_SAFETY_ARM_VALUE, SimClock, build_engine

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"
DT = 1 / 40


@pytest.fixture
def engine(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
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


# --- hold -----------------------------------------------------------------


def test_hold_pins_dormant_past_dwell_expiry(engine):
    engine.machine.hold = True
    run(engine, 0.0, engine.machine.dwell + 30.0)
    assert engine.machine.phase is Phase.DORMANT


def test_hold_blocks_manual_trigger(engine):
    engine.machine.hold = True
    engine.machine.request_eruption()
    run(engine, 0.0, 5.0)
    assert engine.machine.phase is Phase.DORMANT


def test_hold_from_active_returns_to_dormant_immediately(engine):
    run(engine, 0.0, engine.machine.dwell + DT)  # dormant dwell expires
    assert engine.machine.phase is Phase.ACTIVE
    t = engine.machine.entered_at
    engine.machine.hold = True
    engine.step(t + 2 * DT)
    assert engine.machine.phase is Phase.DORMANT


def test_hold_lets_running_eruption_complete(engine):
    engine.machine.request_eruption()
    E = engine.machine.config.imminent_duration
    run(engine, 0.0, E + 1.0)  # into the eruption
    assert engine.machine.phase is Phase.ERUPTION
    engine.machine.hold = True
    run(engine, E + 1.0 + DT, E + engine.machine.config.eruption_duration + 1.0)
    assert engine.machine.phase is Phase.DORMANT  # completed, then pinned


# --- pause ----------------------------------------------------------------


def test_pause_freezes_dwell_and_resume_shifts_it(engine):
    dwell = engine.machine.dwell
    run(engine, 0.0, 10.0)
    engine.machine.request_pause(True)
    run(engine, 10.0 + DT, 10.0 + dwell + 60.0)  # far past the sampled dwell
    assert engine.machine.phase is Phase.DORMANT
    assert engine.machine.elapsed(10.0 + dwell + 60.0) == pytest.approx(10.0, abs=0.1)
    engine.machine.request_pause(False)
    t0 = 10.0 + dwell + 60.0
    run(engine, t0 + DT, t0 + dwell)  # remaining dwell elapses after resume
    assert engine.machine.phase is Phase.ACTIVE


def test_pause_defers_during_imminent(engine):
    engine.machine.request_eruption()
    run(engine, 0.0, 5.0)
    assert engine.machine.phase is Phase.IMMINENT
    engine.machine.request_pause(True)
    E = engine.machine.config.imminent_duration
    run(engine, 5.0 + DT, E + 1.0)
    assert engine.machine.phase is Phase.ERUPTION  # warning ran to completion
    assert engine.machine.pause_pending
    run(engine, E + 1.0 + DT, E + engine.machine.config.eruption_duration + 1.0)
    assert engine.machine.phase is Phase.DORMANT
    assert engine.machine.paused  # engaged once safe


# --- test console ---------------------------------------------------------


def enable_test_mode(engine):
    engine.machine.hold = True
    engine.console.enable()


def test_console_light_override_hits_the_wire_and_clears(engine):
    enable_test_mode(engine)
    par = engine.rig["par3"]
    engine.console.set_channels(
        {par.channel("dimmer"): 255, par.channel("red"): 255, par.channel("green"): 140}
    )
    frame = engine.step(1.0)
    assert frame[par.channel("dimmer") - 1] == 255
    assert frame[par.channel("red") - 1] == 255
    # Blackout base: every other PAR is dark in test mode.
    other = engine.rig["par1"]
    assert frame[other.channel("dimmer") - 1] == 0
    engine.console.disable()
    frame = engine.step(1.0 + DT)  # ambient dormant render owns the wire again
    assert frame[other.channel("dimmer") - 1] > 0


def test_console_refuses_pyro_channels():
    console = TestConsole(pyro_channels={1, 2, 10})
    with pytest.raises(ValueError):
        console.set_channels({1: 130})


def test_console_refuses_overlong_burst():
    console = TestConsole(pyro_channels=set())
    with pytest.raises(ValueError):
        console.request_burst("flame", 2.5)
    with pytest.raises(ValueError):
        console.request_burst("lava", 1.0)


def test_test_burst_fires_through_gate_and_expires(engine):
    engine.gate.arm()
    enable_test_mode(engine)
    engine.console.request_burst("flame", 0.5)
    fire_ch = engine.rig["flame"].channel("fire")
    safety_ch = engine.rig["flame"].channel("safety")
    frames = run(engine, 0.0, 1.0)
    for t, frame in frames:
        if DT <= t < 0.5 - DT:
            assert frame[fire_ch - 1] == 255, f"flame should burn at t={t:.2f}"
            assert frame[safety_ch - 1] == FLAME_SAFETY_ARM_VALUE
        elif t > 0.5 + DT:
            assert frame[fire_ch - 1] == 0, f"flame leaked at t={t:.2f}"
    assert engine.console.last_burst.granted is True


def test_test_burst_denied_when_disarmed(engine):
    enable_test_mode(engine)  # armed is False
    engine.console.request_burst("flame", 0.5)
    frames = run(engine, 0.0, 1.0)
    fire_ch = engine.rig["flame"].channel("fire")
    assert all(f[fire_ch - 1] == 0 for _, f in frames)
    assert engine.console.last_burst.granted is False


def test_co2_test_burst_consumes_tank(engine):
    engine.gate.arm()
    enable_test_mode(engine)
    engine.console.request_burst("co2", 2.0)
    run(engine, 0.0, 0.5)
    assert engine.co2.used_kg == pytest.approx(2.0 * engine.co2.rate_kg_per_s)


def test_console_disable_clears_overrides(engine):
    enable_test_mode(engine)
    par = engine.rig["par1"]
    engine.console.set_channels({par.channel("dimmer"): 200})
    engine.console.disable()
    assert engine.console.overrides() == {}
    assert not engine.console.enabled


# --- sim clock ------------------------------------------------------------


def test_sim_clock_speed_scales_and_rebases(monkeypatch):
    real = [1000.0]
    monkeypatch.setattr("dmx_volcano.show.time.monotonic", lambda: real[0])
    clock = SimClock()
    real[0] += 10.0
    assert clock.now() == pytest.approx(10.0)
    clock.set_speed(60.0)
    real[0] += 1.0
    assert clock.now() == pytest.approx(70.0)  # 10 + 1*60
    clock.set_speed(1.0)
    real[0] += 5.0
    assert clock.now() == pytest.approx(75.0)  # no jump on speed change


def test_sim_clock_clamps_speed():
    clock = SimClock()
    assert clock.set_speed(10_000.0) == SimClock.MAX_SPEED
    assert clock.set_speed(0.0) == SimClock.MIN_SPEED
