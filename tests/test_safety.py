"""The invariants that matter most: no permit, no fire — provable in bytes."""

import pytest

from dmx_volcano.safety import FIRE_WINDOW, MAX_BURST, MIN_GAP, SafetyGate

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


def test_arm_lead_opens_safety_before_fire_and_expires_together():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst(
        "flame", {FLAME_FIRE}, 0.8, now=0.0, arm_channels={FLAME_SAFETY}, arm_lead=0.5
    )
    frame = frame_with_fire()
    gate.clamp(frame, now=0.2)  # inside the lead
    assert frame[FLAME_SAFETY - 1] == 130
    assert frame[FLAME_FIRE - 1] == 0
    frame = frame_with_fire()
    gate.clamp(frame, now=0.7)  # fire window: [0.5, 1.3)
    assert frame[FLAME_SAFETY - 1] == 130
    assert frame[FLAME_FIRE - 1] == 255
    frame = frame_with_fire()
    gate.clamp(frame, now=1.31)
    assert frame[FLAME_SAFETY - 1] == 0
    assert frame[FLAME_FIRE - 1] == 0


def test_arm_lead_never_extends_fire_time():
    gate = armable_gate()
    gate.arm()
    # Max-length burst plus a lead is legal: the lead adds arm time only.
    assert gate.request_burst(
        "flame", {FLAME_FIRE}, MAX_BURST["flame"], now=0.0,
        arm_channels={FLAME_SAFETY}, arm_lead=0.5,
    )
    fire_on = 0
    t = 0.0
    while t < MAX_BURST["flame"] + 1.0:
        frame = frame_with_fire()
        gate.clamp(frame, now=t)
        fire_on += frame[FLAME_FIRE - 1] == 255
        t += 0.025
    assert fire_on == pytest.approx(MAX_BURST["flame"] / 0.025, abs=1)
    with pytest.raises(ValueError):
        gate.request_burst(
            "flame", {FLAME_FIRE}, MAX_BURST["flame"] + 0.1, now=100.0,
            arm_channels={FLAME_SAFETY}, arm_lead=0.5,
        )


def test_min_gap_measured_from_fire_end_to_next_fire_start():
    gate = armable_gate()
    gate.arm()
    # fire window [0.5, 1.3) -> next fire may start at 1.3 + MIN_GAP
    assert gate.request_burst(
        "flame", {FLAME_FIRE}, 0.8, now=0.0, arm_channels={FLAME_SAFETY}, arm_lead=0.5
    )
    ready = 1.3 + MIN_GAP["flame"]
    assert not gate.request_burst(
        "flame", {FLAME_FIRE}, 0.8, now=ready - 0.5 - 0.1,
        arm_channels={FLAME_SAFETY}, arm_lead=0.5,
    )
    assert gate.request_burst(
        "flame", {FLAME_FIRE}, 0.8, now=ready - 0.5 + 0.01,
        arm_channels={FLAME_SAFETY}, arm_lead=0.5,
    )
    with pytest.raises(ValueError):
        gate.request_burst("flame", {FLAME_FIRE}, 0.5, now=100.0, arm_lead=-1.0)


@pytest.mark.parametrize('kind,channel,duration', [('flame', FLAME_FIRE, 10.0), ('co2', CO2_JET, 4.0)])
def test_configured_activation_limit_grants_exact_max_and_rejects_longer(kind, channel, duration):
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst(kind, {channel}, duration, now=0)
    frame = frame_with_fire()
    gate.clamp(frame, now=duration - .025)
    assert frame[channel - 1] == 255
    gate.clamp(frame, now=duration)
    assert frame[channel - 1] == 0
    with pytest.raises(ValueError):
        gate.request_burst(kind, {channel}, duration + .1, now=20)


def test_flame_limits_are_10s_burst_within_10s_per_13s():
    assert MAX_BURST["flame"] == 10.0
    assert FIRE_WINDOW["flame"] == (13.0, 10.0)


def test_full_flame_burst_needs_3s_rest_then_fire_rolls_back_in():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("flame", {FLAME_FIRE}, 10.0, now=0.0)  # [0, 10)
    # Any fire before t=13 would put >10s inside the 13s window ending with it.
    assert not gate.request_burst("flame", {FLAME_FIRE}, 0.1, now=10.0)
    assert not gate.request_burst("flame", {FLAME_FIRE}, 0.1, now=12.9)
    assert gate.gap_remaining("flame", now=10.0) == pytest.approx(3.0, abs=1e-6)
    # From t=13 the old burst rolls out of the window as fast as new fire
    # rolls in: window ending 13+d holds (10 - d) old + d new = 10s.
    assert gate.request_burst("flame", {FLAME_FIRE}, 2.0, now=13.0)  # [13, 15)
    assert gate.request_burst("flame", {FLAME_FIRE}, 0.5, now=15.0)
    # Same again: fire may continue until 10s are back-to-back (13 -> 23),
    # i.e. sustained use settles into 10s on / 3s off.
    assert gate.fire_budget("flame", 15.5) == pytest.approx(7.5, abs=1e-6)


def test_short_flame_taps_need_no_gap_until_budget_is_spent():
    gate = armable_gate()
    gate.arm()
    for start in (0.0, 2.0, 4.0, 6.0, 8.0):
        assert gate.request_burst("flame", {FLAME_FIRE}, 2.0, now=start)
    # 10s fired in [0, 10); the next fire must wait until t=13.
    assert not gate.request_burst("flame", {FLAME_FIRE}, 0.5, now=10.0)
    assert gate.request_burst("flame", {FLAME_FIRE}, 0.5, now=13.0)


def test_overlapping_flame_bursts_are_denied():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("flame", {FLAME_FIRE}, 2.0, now=0.0)
    assert not gate.request_burst("flame", {FLAME_FIRE}, 1.0, now=1.0)


def test_no_13s_window_ever_exceeds_10s_of_fire():
    """Greedy worst case: request random bursts every frame for two minutes,
    then measure the granted fire intervals exactly (frame sampling would add
    up to one frame per short burst)."""
    import random

    rng = random.Random(7)
    gate = armable_gate()
    gate.arm()
    granted = []
    t = 0.0
    while t < 120.0:
        duration = rng.choice([0.2, 0.5, 1.0, 3.0, 7.0, 10.0])
        if gate.request_burst("flame", {FLAME_FIRE}, duration, now=t):
            granted.append((t, t + duration))
        t += 0.025

    window, budget = FIRE_WINDOW["flame"]

    def fire_in(a: float) -> float:
        return sum(max(0.0, min(e, a + window) - max(s, a)) for s, e in granted)

    # The max over windows is reached with an edge on some burst edge.
    edges = [s for s, _ in granted] + [e - window for _, e in granted]
    worst = max(fire_in(a) for a in edges)
    assert worst <= budget + 1e-6
    assert worst >= budget - 0.5  # and the greedy client really uses the budget


def test_ending_flame_early_books_only_actual_fire():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("flame", {FLAME_FIRE}, 10.0, now=0.0)
    gate.end_burst("flame", {FLAME_FIRE}, now=1.0)
    frame = frame_with_fire()
    gate.clamp(frame, now=1.5)
    assert frame[FLAME_FIRE - 1] == 0
    # Only 1s was fired, so 9s more is available immediately.
    assert gate.fire_budget("flame", 1.0) == pytest.approx(9.0, abs=1e-6)
    assert gate.request_burst("flame", {FLAME_FIRE}, 9.0, now=1.0)
    assert not gate.request_burst("flame", {FLAME_FIRE}, 0.5, now=10.0)


def test_disarm_does_not_reset_the_flame_budget():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("flame", {FLAME_FIRE}, 10.0, now=0.0)
    gate.disarm()
    gate.arm()
    assert not gate.request_burst("flame", {FLAME_FIRE}, 1.0, now=10.5)


def test_co2_early_end_keeps_reserved_gap():
    gate = armable_gate()
    gate.arm()
    assert gate.request_burst("co2", {CO2_JET}, 4.0, now=0.0)
    gate.end_burst("co2", {CO2_JET}, now=1.0)
    assert not gate.request_burst("co2", {CO2_JET}, 1.0, now=4.0 + MIN_GAP["co2"] - 0.1)
