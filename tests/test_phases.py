import numpy as np
import pytest

from dmx_volcano.phases import Phase, PhaseConfig, PhaseMachine, Transition
from dmx_volcano.stats import DwellConfig

DWELL = {
    Phase.DORMANT: DwellConfig(t_min=10, t_max=20, k=2.0, scale=15),
    Phase.ACTIVE: DwellConfig(t_min=5, t_max=10, k=2.0, scale=7),
}


def make_machine(p_erupt: float, seed: int = 0) -> tuple[PhaseMachine, list[Transition]]:
    events: list[Transition] = []
    machine = PhaseMachine(
        config=PhaseConfig(
            dwell=DWELL, p_erupt=p_erupt, eruption_duration=3.0, cooldown=30.0
        ),
        rng=np.random.default_rng(seed),
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


def test_active_always_erupts_at_p1_and_fizzles_at_p0():
    for p, expected in [(1.0, Phase.ERUPTION), (0.0, Phase.DORMANT)]:
        machine, events = make_machine(p_erupt=p)
        machine.tick(machine.dwell + 0.1)  # -> ACTIVE
        machine.tick(machine.entered_at + machine.dwell + 0.1)
        assert machine.phase is expected
        if p == 0.0:
            assert events[-1].cause == "fizzle"


def test_eruption_ends_after_duration_with_cooldown():
    machine, _ = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)
    assert machine.phase is Phase.ERUPTION
    machine.tick(1.0 + 3.0)
    assert machine.phase is Phase.DORMANT
    assert machine.cooldown_until == pytest.approx(4.0 + 30.0)


def test_manual_trigger_blocked_during_cooldown():
    machine, events = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)
    machine.tick(4.5)  # eruption over, in cooldown
    machine.request_eruption()
    machine.tick(5.0)
    assert machine.phase is Phase.DORMANT
    assert all(e.to is not Phase.ERUPTION for e in events[-1:])


def test_manual_trigger_cause_logged():
    machine, events = make_machine(p_erupt=1.0)
    machine.request_eruption()
    machine.tick(1.0)
    assert events[-1].cause == "manual"
    assert events[-1].to is Phase.ERUPTION
