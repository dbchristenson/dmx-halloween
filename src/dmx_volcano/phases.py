"""The dormant / active / imminent / eruption state machine.

- Dormant and active sample their dwell at phase entry (stats.py).
- Active escalates with p_erupt, else fizzles back to dormant.
- Escalation always passes through IMMINENT: a fixed-length, unmistakable
  warning look that tells visitors to pay attention *before* any pyro.
  Manual triggers route through it too — nobody, operator included, can
  skip the warning. It cannot fizzle: imminent always erupts.
- Eruption is a fixed-length scripted cue (cues.py), then a mandatory
  cooldown back in dormant.
- Manual triggers are *requests*: they route through the same gates
  (cooldown, current phase) as automatic transitions.
- Operator overrides: `hold` pins the show in dormant (an in-progress
  imminent/eruption still completes first — the warning contract survives);
  pause freezes phase timing, but only in dormant/active.

Every transition is reported to the event log with the sampled dwell vs.
actual elapsed and the trigger cause — the raw material for night-to-night
retuning.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum

import numpy as np

from .stats import DwellConfig, sample_dwell


class Phase(Enum):
    DORMANT = "dormant"
    ACTIVE = "active"
    IMMINENT = "imminent"
    ERUPTION = "eruption"


@dataclass
class PhaseConfig:
    dwell: dict[Phase, DwellConfig]
    p_erupt: float = 0.7  # from ACTIVE: escalate vs. fizzle
    imminent_duration: float = 30.0  # fixed warning window before eruption
    eruption_duration: float = 20.0  # length of the scripted cue, seconds
    cooldown: float = 60.0  # min time after eruption before leaving dormant


@dataclass
class Transition:
    frm: Phase
    to: Phase
    cause: str  # "timer" | "fizzle" | "manual"
    sampled_dwell: float | None
    actual_elapsed: float


@dataclass
class PhaseMachine:
    config: PhaseConfig
    rng: np.random.Generator
    on_transition: Callable[[Transition], None] = lambda t: None

    phase: Phase = Phase.DORMANT
    entered_at: float = 0.0
    dwell: float | None = None
    cooldown_until: float = float("-inf")
    # Operator overrides. hold/_pause_target are written by the web thread
    # (atomic bool stores); every transition and timer mutation still happens
    # only inside tick(), on the frame thread.
    hold: bool = False  # while True the show never leaves dormant on its own
    _manual_requested: bool = field(default=False, repr=False)
    _pause_target: bool = field(default=False, repr=False)
    _paused_at: float | None = field(default=None, repr=False)

    def start(self, now: float) -> None:
        self._enter(Phase.DORMANT, now, cause="start")

    def request_eruption(self) -> None:
        """Manual trigger from the web page; honored on the next tick if gates allow."""
        self._manual_requested = True

    def request_pause(self, on: bool) -> None:
        """Freeze/unfreeze phase timing. Takes effect on the next tick, and
        only in dormant/active — imminent and eruption always run to
        completion (the warning can't be stretched around live pyro)."""
        self._pause_target = on

    @property
    def paused(self) -> bool:
        return self._paused_at is not None

    @property
    def pause_pending(self) -> bool:
        """Pause requested but deferred until the show is back in dormant/active."""
        return self._pause_target and not self.paused

    def elapsed(self, now: float) -> float:
        """Time in phase, frozen while paused."""
        return (self._paused_at if self.paused else now) - self.entered_at

    def tick(self, now: float) -> None:
        if self._paused_at is not None:
            if self._pause_target:
                return
            # Resume: shift every timer by the paused span so no dwell elapses.
            shift = now - self._paused_at
            self.entered_at += shift
            if self.cooldown_until > float("-inf"):
                self.cooldown_until += shift
            self._paused_at = None
        elif self._pause_target and self.phase in (Phase.DORMANT, Phase.ACTIVE):
            self._paused_at = now
            return

        if self.hold:
            self._manual_requested = False
            if self.phase is Phase.ACTIVE:
                self._transition(Phase.DORMANT, now, "hold")
                return

        elapsed = now - self.entered_at

        if self.phase is Phase.ERUPTION:
            self._manual_requested = False
            if elapsed >= self.config.eruption_duration:
                self.cooldown_until = now + self.config.cooldown
                self._transition(Phase.DORMANT, now, "timer")
            return

        if self.phase is Phase.IMMINENT:
            self._manual_requested = False  # already committed to erupting
            if elapsed >= self.config.imminent_duration:
                self._transition(Phase.ERUPTION, now, "timer")
            return

        if self._manual_requested:
            self._manual_requested = False
            if now >= self.cooldown_until:
                self._transition(Phase.IMMINENT, now, "manual")
                return

        if self.dwell is not None and elapsed >= self.dwell:
            if self.phase is Phase.DORMANT:
                if not self.hold and now >= self.cooldown_until:
                    self._transition(Phase.ACTIVE, now, "timer")
            elif self.rng.random() < self.config.p_erupt:
                self._transition(Phase.IMMINENT, now, "timer")
            else:
                self._transition(Phase.DORMANT, now, "fizzle")

    def _transition(self, to: Phase, now: float, cause: str) -> None:
        self.on_transition(
            Transition(
                frm=self.phase,
                to=to,
                cause=cause,
                sampled_dwell=self.dwell,
                actual_elapsed=now - self.entered_at,
            )
        )
        self._enter(to, now, cause)

    def _enter(self, phase: Phase, now: float, cause: str) -> None:
        self.phase = phase
        self.entered_at = now
        cfg = self.config.dwell.get(phase)
        self.dwell = sample_dwell(cfg, self.rng) if cfg else None
