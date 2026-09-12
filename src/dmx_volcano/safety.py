"""Safety gate — sits BELOW the cue layer; no script can bypass it.

Invariants enforced here (see DESIGN.md "Safety invariants"):
- Pyro channels are clamped to 0 unless BOTH pyro is enabled AND an
  unexpired, bounded permit covers the channel.
- Permits are time-boxed at issue; requesting one longer than the fixture's
  max burst raises rather than truncates (a cue asking for too much fire is
  a bug to surface, not accommodate).
- Minimum gaps between bursts are enforced per fixture, measured from the
  end of one trigger window to the start of the next.
- A burst may open an *arm* channel ahead of the trigger channel (the H-E01A
  refuses to fire unless its safety channel is already open when the fire
  channel rises). The arm channels are permitted from the request, the
  trigger channels only from `now + arm_lead`; both permits expire together.
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
    starts_at: float = float("-inf")  # clamped until then, even if issued


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
        self,
        kind: str,
        channels: set[int],
        duration: float,
        now: float | None = None,
        *,
        arm_channels: set[int] = frozenset(),
        arm_lead: float = 0.0,
    ) -> bool:
        """Ask permission for a time-boxed pyro burst. Returns False if denied.

        `channels` (the trigger) are permitted for `duration` seconds starting
        `arm_lead` after now; `arm_channels` are permitted from now until the
        same end. `duration` is the trigger time and is what MAX_BURST bounds;
        the lead only ever adds arm-channel time, never fire time."""
        now = time.monotonic() if now is None else now
        if not self.pyro_enabled:
            return False
        if duration > MAX_BURST[kind]:
            raise ValueError(
                f"{kind} burst of {duration}s exceeds max {MAX_BURST[kind]}s"
            )
        if arm_lead < 0:
            raise ValueError(f"arm_lead must be >= 0, got {arm_lead}")
        fire_start = now + arm_lead
        end = fire_start + duration
        if fire_start - self._last_burst_end.get(kind, float("-inf")) < MIN_GAP[kind]:
            return False
        if arm_channels:
            self._permits.append(
                Permit(channels=set(arm_channels), starts_at=now, expires_at=end)
            )
        self._permits.append(
            Permit(channels=set(channels), starts_at=fire_start, expires_at=end)
        )
        self._last_burst_end[kind] = end
        return True

    def clamp(self, frame: bytearray, now: float | None = None) -> None:
        """Zero every pyro channel not covered by a live permit. Mutates frame
        in place; called on every frame of the 40Hz loop, 1-indexed channels."""
        now = time.monotonic() if now is None else now
        self._permits = [p for p in self._permits if p.expires_at > now]
        permitted: set[int] = set()
        if self.pyro_enabled:
            for permit in self._permits:
                if permit.starts_at <= now:
                    permitted |= permit.channels
        for channel in self.pyro_channels - permitted:
            frame[channel - 1] = 0
