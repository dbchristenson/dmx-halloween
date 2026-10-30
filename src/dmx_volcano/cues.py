"""Scripted cue timelines.

The eruption carries no flame: the CO2 would smother it, and lingering CO2
can stop it igniting (flame belongs to transitions and imminent — flame.py).

Eruption is deliberately NOT stochastic: it is a deterministic list of
(time, action) pairs so timing precision and safety bounds are auditable.
Actions describe *intent*; the show loop translates them into channel values
and, for pyro, into safety-gate burst requests that may be denied.
"""

from dataclasses import dataclass, field

from .safety import MAX_BURST


@dataclass(frozen=True)
class Cue:
    t: float  # seconds from sequence start
    action: str  # e.g. "sound", "pars", "fog", "co2_burst"
    params: dict = field(default_factory=dict)


def load_timeline(entries: list[dict]) -> list[Cue]:
    """Build a timeline from config ([[cues.eruption]] tables in show.toml).
    Every key besides t/action becomes a cue param."""
    return [
        Cue(
            t=float(e["t"]),
            action=e["action"],
            params={k: v for k, v in e.items() if k not in ("t", "action")},
        )
        for e in entries
    ]


# action -> (required param, burst kind or None)
ACTIONS = {
    "sound": ("file", None),
    "fog": ("level", None),
    "co2_burst": ("duration", "co2"),
    "pars": (None, None),
    "fade": (None, None),
}


def validate_timeline(timeline: list[Cue]) -> None:
    """Fail at startup on anything the show loop would trip over mid-eruption.

    The gate checks arming before burst length, so an overlong pyro cue only
    raises once armed — every rehearsal passes and the first armed night
    crashes. Check it here instead, armed or not."""
    for cue in timeline:
        where = f"cue at t={cue.t} ({cue.action})"
        if cue.action not in ACTIONS:
            raise ValueError(f"{where}: unknown action (known: {', '.join(ACTIONS)})")
        if cue.t < 0:
            raise ValueError(f"{where}: t must be >= 0")
        param, kind = ACTIONS[cue.action]
        if param is not None and param not in cue.params:
            raise ValueError(f"{where}: missing {param!r}")
        if kind is not None and not 0 < cue.params["duration"] <= MAX_BURST[kind]:
            raise ValueError(f"{where}: duration must be 0-{MAX_BURST[kind]}s")
        if cue.action == "sound":
            files = cue.params["file"]
            files = [files] if isinstance(files, str) else files
            if not files or not all(isinstance(f, str) for f in files):
                raise ValueError(f"{where}: file must be a filename or a list of them")
        if cue.action == "fog" and not 0 <= cue.params["level"] <= 255:
            raise ValueError(f"{where}: level must be 0-255")


# Fallback timeline if config has no [[cues.eruption]] — tune in config, not
# here. CO2 cues must respect safety.MAX_BURST and MIN_GAP (measured
# burst-end to burst-start) or the gate will deny them — the gate wins.
ERUPTION = [
    Cue(1.0, "co2_burst", {"duration": 0.5}),
    Cue(6.5, "co2_burst", {"duration": 5.0}),  # >= 5s after the pop ends
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
