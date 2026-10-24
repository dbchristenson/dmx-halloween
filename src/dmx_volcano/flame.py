"""Flame patterns: flame balls fired in morse-like rhythms.

A pattern is a string of '.' (dot) and '-' (dash) pulses. Spaces are
ignored, so "- . . -" and "-..-" are the same pattern. [flame] in show.toml
sets the pulse lengths and the gap between pulses, and holds the pools:

- [flame.transitions] "dormant->active" = [...]: fired when the show makes
  exactly that move, at the start of the new phase. An operator hold's
  active->dormant fires nothing.
- "imminent->eruption" is the finale. It plays at the END of imminent and
  finishes `clear_before_co2` seconds before the eruption's first CO2 cue.
- [flame.imminent] patterns / rest: imminent's own flame, packed between its
  entry pattern and the finale with a random rest between patterns.

No pattern may start in the eruption or after it ("eruption->..." keys are
refused): CO2 pools in the crater and can stop the flame igniting, which
would release unburnt gas. The SafetyGate refuses the same thing on its own
(FLAME_AFTER_CO2).

Each pattern goes to the gate as one sequence: the safety channel opens
`lead` seconds ahead of the first pulse and holds through the last; the fire
channel is permitted only inside a pulse. Every pick is random and never
repeats the previous pick while the pool has alternatives.
"""

import random
from dataclasses import dataclass, field

from .safety import FIRE_WINDOW, MAX_BURST, MIN_PULSE

# The moves the phase machine can make that may carry flame.
MOVES = {
    ("dormant", "active"),
    ("active", "dormant"),
    ("active", "imminent"),
    ("dormant", "imminent"),
    ("imminent", "eruption"),
}
FINALE = ("imminent", "eruption")


@dataclass(frozen=True)
class Timing:
    dot: float = 0.15
    dash: float = 0.45
    gap: float = 0.25


def parse_pattern(pattern: str, timing: Timing) -> list[tuple[float, float]]:
    """Pulses as (offset from the first fire, duration). Raises on anything
    the gate would refuse, so a bad pattern fails at startup, not mid-show."""
    pulses: list[tuple[float, float]] = []
    t = 0.0
    for ch in pattern:
        if ch.isspace():
            continue
        if ch not in ".-":
            raise ValueError(f"flame pattern {pattern!r}: use only '.', '-' and spaces")
        duration = timing.dot if ch == "." else timing.dash
        pulses.append((t, duration))
        t += duration + timing.gap
    if not pulses:
        raise ValueError(f"flame pattern {pattern!r} has no pulses")
    for _, duration in pulses:
        if not MIN_PULSE["flame"] <= duration <= MAX_BURST["flame"]:
            raise ValueError(
                f"flame pulse of {duration}s outside "
                f"{MIN_PULSE['flame']}-{MAX_BURST['flame']}s"
            )
    if timing.gap < 0:
        raise ValueError("flame gap must be >= 0")
    if sum(d for _, d in pulses) > FIRE_WINDOW["flame"][1]:
        raise ValueError(f"flame pattern {pattern!r} exceeds the fire budget on its own")
    return pulses


def length(pulses: list[tuple[float, float]]) -> float:
    """First fire to last fire end, seconds."""
    offset, duration = pulses[-1]
    return offset + duration


@dataclass
class Shot:
    """One pattern scheduled at `start` (when the safety channel opens)."""

    start: float
    pattern: str
    pulses: list[tuple[float, float]]
    source: str  # e.g. "dormant->active", "imminent", "test"

    def fire_windows(self, lead: float) -> list[tuple[float, float]]:
        first = self.start + lead
        return [(first + o, first + o + d) for o, d in self.pulses]

    def end(self, lead: float) -> float:
        return self.start + lead + length(self.pulses)


@dataclass
class FlameConfig:
    timing: Timing = field(default_factory=Timing)
    transitions: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    imminent: list[str] = field(default_factory=list)
    rest: tuple[float, float] = (1.0, 3.0)
    clear_before_co2: float = 1.5

    @classmethod
    def from_config(cls, raw: dict) -> "FlameConfig":
        """Build from the [flame] table; validates every pattern."""
        timing = Timing(**{k: float(raw[k]) for k in ("dot", "dash", "gap") if k in raw})
        transitions: dict[tuple[str, str], list[str]] = {}
        for key, pool in raw.get("transitions", {}).items():
            frm, sep, to = key.partition("->")
            move = (frm.strip(), to.strip())
            if not sep:
                raise ValueError(f"[flame.transitions] key must look like 'dormant->active', got {key!r}")
            if move[0] == "eruption":
                raise ValueError(
                    f"[flame.transitions] {key!r}: no flame after the eruption — "
                    "lingering CO2 can stop it igniting"
                )
            if move not in MOVES:
                raise ValueError(f"[flame.transitions] {key!r} is not a move the show makes")
            transitions[move] = list(pool)
        imminent = raw.get("imminent", {})
        rest = tuple(float(x) for x in imminent.get("rest", (1.0, 3.0)))
        if len(rest) != 2 or not 0 <= rest[0] <= rest[1]:
            raise ValueError("[flame.imminent] rest must be [min, max] seconds")
        cfg = cls(
            timing=timing,
            transitions=transitions,
            imminent=list(imminent.get("patterns", [])),
            rest=rest,
            clear_before_co2=float(raw.get("clear_before_co2", 1.5)),
        )
        for pattern in cfg.patterns():
            parse_pattern(pattern, timing)
        return cfg

    def patterns(self) -> list[str]:
        return [p for pool in self.transitions.values() for p in pool] + self.imminent

    def check_finale(self, lead: float, imminent_duration: float, first_co2: float) -> None:
        """Every finale must fit inside imminent and still clear the first
        CO2 cue — checked at startup like the cue timeline."""
        room = min(imminent_duration, imminent_duration + first_co2 - self.clear_before_co2)
        for pattern in self.transitions.get(FINALE, []):
            need = lead + length(parse_pattern(pattern, self.timing))
            if need > room:
                raise ValueError(
                    f"flame finale {pattern!r} needs {need:.2f}s but imminent leaves "
                    f"{room:.2f}s before clearing the first CO2 cue"
                )


@dataclass
class FlamePlanner:
    """Turns phase moves into scheduled Shots."""

    config: FlameConfig
    lead: float
    imminent_duration: float
    first_co2: float  # eruption-relative time of the first CO2 cue (inf: none)
    rng: random.Random = field(default_factory=random.Random)
    _last: dict[str, str] = field(default_factory=dict, repr=False)

    def plan(self, frm: str, to: str, cause: str, now: float) -> list[Shot]:
        if cause == "hold" or (frm, to) == FINALE:
            return []  # the finale was already scheduled inside imminent
        entry = self._shot(f"{frm}->{to}", self.config.transitions.get((frm, to)), now)
        if to != "imminent":
            return [entry] if entry else []
        return self._plan_imminent(entry, now)

    def _plan_imminent(self, entry: Shot | None, now: float) -> list[Shot]:
        """Entry pattern, then imminent's pool back to back with rests, then
        the finale, which is pinned to finish clear of the first CO2 cue."""
        d = self.imminent_duration
        done_by = now + min(d, d + self.first_co2 - self.config.clear_before_co2)
        finale = self._shot("imminent->eruption", self.config.transitions.get(FINALE), now)
        limit = done_by
        if finale:
            finale.start = done_by - self.lead - length(finale.pulses)
            if finale.start < now:
                finale = None
            else:
                limit = finale.start - self.config.rest[0]
        shots = []
        t = now
        if entry and entry.end(self.lead) <= limit:
            shots.append(entry)
            t = entry.end(self.lead) + self.rng.uniform(*self.config.rest)
        while self.config.imminent:
            shot = self._shot("imminent", self.config.imminent, t)
            if shot.end(self.lead) > limit:
                break
            shots.append(shot)
            t = shot.end(self.lead) + self.rng.uniform(*self.config.rest)
        if finale:
            shots.append(finale)
        return shots

    def _shot(self, key: str, pool: list[str] | None, start: float) -> Shot | None:
        if not pool:
            return None
        fresh = [p for p in pool if p != self._last.get(key)] or pool
        pattern = self.rng.choice(fresh)
        self._last[key] = pattern
        return Shot(start, pattern, parse_pattern(pattern, self.config.timing), key)
