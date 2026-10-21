import random

import pytest

from dmx_volcano.phases import Phase, PhaseConfig, PhaseMachine, Transition
from dmx_volcano.stats import DwellConfig

DWELL = {
    Phase.DORMANT: DwellConfig(t_min=10, t_max=20, k=2.0, scale=15),
    Phase.ACTIVE: DwellConfig(t_min=5, t_max=10, k=2.0, scale=7),
}
IMMINENT_S = 2.0
ERUPTION_S = 3.0
COOLDOWN_S = 30.0


def make_machine(p_erupt: float, seed: int = 0) -> tuple[PhaseMachine, list[Transition]]:
    events: list[Transition] = []
    machine = PhaseMachine(
        config=PhaseConfig(
            dwell=DWELL,
            p_erupt=p_erupt,
            imminent_duration=IMMINENT_S,
            eruption_duration=ERUPTION_S,
            cooldown=COOLDOWN_S,
        ),
        rng=random.Random(seed),
        on_transition=events.append,
    )
    machine.start(0.0)
    return machine, events


def test_dormant_advances_to_active_after_dwell():
    machine, _ = make_machine(p_erupt=1.0)
    machine.tick(machine.dwell - 0.1)
    assert machine.phase is Phase.DORMANT
    machine.tick(machine.dwell + 0.1)
    assert machine.phase is Phase.ACTIVE


def test_active_escalates_to_imminent_at_p1_and_fizzles_at_p0():
    for p, expected in [(1.0, Phase.IMMINENT), (0.0, Phase.DORMANT)]:
        machine, events = make_machine(p_erupt=p)
        machine.tick(machine.dwell + 0.1)  # -> ACTIVE
        machine.tick(machine.entered_at + machine.dwell + 0.1)
        assert machine.phase is expected
        if p == 0.0:
            assert events[-1].cause == "fizzle"


def test_imminent_always_erupts_after_fixed_duration():
    machine, events = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)
    assert machine.phase is Phase.IMMINENT
    # A manual request during imminent must not shortcut the warning window.
    machine.request_eruption()
    machine.tick(1.0 + IMMINENT_S - 0.1)
    assert machine.phase is Phase.IMMINENT
    machine.tick(1.0 + IMMINENT_S + 0.1)
    assert machine.phase is Phase.ERUPTION
    assert events[-1].cause == "timer"


def test_eruption_ends_after_duration_with_cooldown():
    machine, _ = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)  # -> IMMINENT
    machine.tick(1.0 + IMMINENT_S)  # -> ERUPTION
    eruption_start = machine.entered_at
    machine.tick(eruption_start + ERUPTION_S + 0.1)
    assert machine.phase is Phase.DORMANT
    assert machine.cooldown_until == pytest.approx(
        eruption_start + ERUPTION_S + 0.1 + COOLDOWN_S
    )


def test_manual_trigger_blocked_during_cooldown():
    machine, events = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)
    machine.tick(1.0 + IMMINENT_S)
    machine.tick(1.0 + IMMINENT_S + ERUPTION_S + 0.1)  # eruption over -> cooldown
    assert machine.phase is Phase.DORMANT
    machine.request_eruption()
    machine.tick(machine.cooldown_until - 1.0)
    assert machine.phase is Phase.DORMANT
    assert all(e.to is not Phase.IMMINENT for e in events[-1:])


def test_manual_trigger_enters_imminent_with_cause_logged():
    machine, events = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)
    assert events[-1].cause == "manual"
    assert events[-1].to is Phase.IMMINENT
