"""Scripted cue timelines.

Eruption is deliberately NOT stochastic: it is a deterministic list of
(time, action) pairs so timing precision and safety bounds are auditable.
Actions describe *intent*; the show loop translates them into channel values
and, for pyro, into safety-gate burst requests that may be denied.
"""

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Cue:
    t: float  # seconds from sequence start
    action: str  # e.g. "sound", "pars", "fog", "co2_burst", "flame_burst"
    params: dict = field(default_factory=dict)


# Placeholder timeline — tune against the real rig during rehearsals.
# flame_burst durations must stay within safety.MAX_BURST["flame"].
ERUPTION = [
    Cue(0.0, "sound", {"file": "eruption.wav"}),
    Cue(0.0, "pars", {"color": (255, 30, 0), "strobe_hz": 8}),
    Cue(2.0, "fog", {"level": 255}),
    Cue(4.0, "co2_burst", {"duration": 1.5}),
    Cue(5.0, "flame_burst", {"duration": 0.8}),
    Cue(7.5, "flame_burst", {"duration": 0.8}),
    Cue(12.0, "fade", {"to": "dormant", "over": 8.0}),
]


class CueRunner:
    """Feeds due cues to a handler as time advances through a timeline."""

    def __init__(self, timeline: list[Cue]) -> None:
        self._timeline = sorted(timeline, key=lambda c: c.t)
        self._next = 0
        self._started_at: float | None = None

    def start(self, now: float) -> None:
        self._started_at = now
        self._next = 0

    @property
    def running(self) -> bool:
        return self._started_at is not None and self._next < len(self._timeline)

    def due_cues(self, now: float) -> list[Cue]:
        if self._started_at is None:
            return []
        elapsed = now - self._started_at
        due = []
        while self._next < len(self._timeline) and self._timeline[self._next].t <= elapsed:
            due.append(self._timeline[self._next])
            self._next += 1
        return due
