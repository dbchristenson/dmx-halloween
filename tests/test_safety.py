"""The invariants that matter most: no permit, no fire — provable in bytes."""

import pytest

from dmx_volcano.safety import MAX_BURST, MIN_GAP, SafetyGate

FLAME_SAFETY, FLAME_FIRE, CO2_JET = 1, 2, 10
PYRO = {FLAME_SAFETY, FLAME_FIRE, CO2_JET}


def frame_with_fire() -> bytearray:
    frame = bytearray(512)
    frame[FLAME_SAFETY - 1] = 130  # inside H-E01A arm window
    frame[FLAME_FIRE - 1] = 255
    frame[CO2_JET - 1] = 255
    return frame


def armable_gate() -> SafetyGate:
    """A gate whose config interlock is open (show-night pyro_enabled=true)."""
    return SafetyGate(pyro_channels=PYRO, enabled_by_config=True)


def test_arm_refused_while_config_interlock_off():
    gate = SafetyGate(pyro_channels=PYRO)  # rehearsal: enabled_by_config False
    assert gate.arm() is False
    assert gate.pyro_enabled is False
    frame = frame_with_fire()
    gate.clamp(frame, now=0.0)
    assert frame[FLAME_FIRE - 1] == 0


def test_disarmed_gate_zeroes_pyro_even_if_cue_wrote_values():
    gate = SafetyGate(pyro_channels=PYRO)
    frame = frame_with_fire()
    gate.clamp(frame, now=0.0)
    assert frame[FLAME_SAFETY - 1] == 0
    assert frame[FLAME_FIRE - 1] == 0
    assert frame[CO2_JET - 1] == 0


def test_gate_starts_disarmed():
    assert SafetyGate(pyro_channels=PYRO).pyro_enabled is False


def test_armed_without_permit_still_clamps():
    gate = armable_gate()
    gate.arm()
    frame = frame_with_fire()
    gate.clamp(frame, now=0.0)
    assert frame[FLAME_FIRE - 1] == 0


def test_permit_allows_then_expires():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("flame", {FLAME_SAFETY, FLAME_FIRE}, 0.8, now=0.0)
    frame = frame_with_fire()
    gate.clamp(frame, now=0.5)
    assert frame[FLAME_FIRE - 1] == 255  # inside the permit window
    frame = frame_with_fire()
    gate.clamp(frame, now=0.9)
    assert frame[FLAME_FIRE - 1] == 0  # expired


def test_burst_denied_when_disarmed():
    gate = SafetyGate(pyro_channels=PYRO)
    assert not gate.request_burst("flame", {FLAME_FIRE}, 0.5, now=0.0)


def test_overlong_burst_raises():
    gate = armable_gate()
    gate.arm()
    with pytest.raises(ValueError):
        gate.request_burst("flame", {FLAME_FIRE}, MAX_BURST["flame"] + 0.1, now=0.0)


def test_min_gap_between_bursts():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("co2", {CO2_JET}, 2.0, now=0.0)  # ends t=2
    assert not gate.request_burst("co2", {CO2_JET}, 2.0, now=2.0 + MIN_GAP["co2"] - 1)
    assert gate.request_burst("co2", {CO2_JET}, 2.0, now=2.0 + MIN_GAP["co2"] + 0.1)


def test_disarm_revokes_live_permits():
    gate = armable_gate()
    gate.arm()
    gate.request_burst("flame", {FLAME_FIRE}, 0.8, now=0.0)
    gate.disarm()
    frame = frame_with_fire()
    gate.clamp(frame, now=0.1)
    assert frame[FLAME_FIRE - 1] == 0


def test_non_pyro_channels_untouched():
    gate = SafetyGate(pyro_channels=PYRO)
    frame = bytearray(512)
    frame[100] = 200  # a PAR channel
    gate.clamp(frame, now=0.0)
    assert frame[100] == 200
