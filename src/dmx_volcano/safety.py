"""Safety gate — sits BELOW the cue layer; no script can bypass it.

Invariants enforced here (see DESIGN.md "Safety invariants"):
- Pyro channels are clamped to 0 unless BOTH pyro is enabled AND an
  unexpired, bounded permit covers the channel.
- Permits are time-boxed at issue; requesting one longer than the fixture's
  max burst raises rather than truncates (a cue asking for too much fire is
  a bug to surface, not accommodate).
- Minimum gaps between bursts are enforced per fixture.
- Disarming (or restart — arming state is never persisted) drops all permits.
- The config pyro_enabled flag is a rehearsal interlock enforced here, not in
  the web layer: arm() refuses while it is off.

CO2 limits per MOKA MK-C09 documentation: bursts <= 10s, >= 5s between.
Flame limits are deliberately tighter still.
"""

import time
from dataclasses import dataclass, field

# Per-fixture operational limits, seconds.
MAX_BURST = {"flame": 2.0, "co2": 10.0}
MIN_GAP = {"flame": 3.0, "co2": 5.0}


@dataclass
class Permit:
    channels: set[int]
    expires_at: float


@dataclass
class SafetyGate:
    pyro_channels: set[int]
    # Rehearsal interlock (invariant 3): the show.toml pyro_enabled flag.
    # While False, arm() refuses — pyro stays clamped even with hardware
    # armed. Flipping it requires a config edit + restart, never a web call.
    enabled_by_config: bool = False
    pyro_enabled: bool = False  # the software arm switch; always starts False
    _permits: list[Permit] = field(default_factory=list)
    _last_burst_end: dict[str, float] = field(default_factory=dict)

    def arm(self) -> bool:
        """The web half of two-step arming. Refused in rehearsal mode."""
        if not self.enabled_by_config:
            return False
        self.pyro_enabled = True
        return True

    def disarm(self) -> None:
        self.pyro_enabled = False
        self._permits.clear()

    def gap_remaining(self, kind: str, now: float | None = None) -> float:
        """Seconds until the next burst of `kind` may be granted (0 = ready)."""
        now = time.monotonic() if now is None else now
        last_end = self._last_burst_end.get(kind, float("-inf"))
        return max(0.0, last_end + MIN_GAP[kind] - now)

    def request_burst(
        self, kind: str, channels: set[int], duration: float, now: float | None = None
    ) -> bool:
        """Ask permission for a time-boxed pyro burst. Returns False if denied."""
        now = time.monotonic() if now is None else now
        if not self.pyro_enabled:
            return False
        if duration > MAX_BURST[kind]:
            raise ValueError(
                f"{kind} burst of {duration}s exceeds max {MAX_BURST[kind]}s"
            )
        if now - self._last_burst_end.get(kind, float("-inf")) < MIN_GAP[kind]:
            return False
        self._permits.append(Permit(channels=channels, expires_at=now + duration))
        self._last_burst_end[kind] = now + duration
        return True

    def clamp(self, frame: bytearray, now: float | None = None) -> None:
        """Zero every pyro channel not covered by a live permit. Mutates frame
        in place; called on every frame of the 40Hz loop, 1-indexed channels."""
        now = time.monotonic() if now is None else now
        self._permits = [p for p in self._permits if p.expires_at > now]
        permitted: set[int] = set()
        if self.pyro_enabled:
            for permit in self._permits:
                permitted |= permit.channels
        for channel in self.pyro_channels - permitted:
            frame[channel - 1] = 0
