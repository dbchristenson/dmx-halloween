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
- Thread-safe: the web thread arms/disarms while the frame loop requests and
  clamps. One lock serializes them, so a disarm can never be undone by a
  clamp() that was mid-way through rebuilding the permit list.

Show activation limits: flame <= 3s, CO2 <= 4s.
Minimum gaps remain 3s for flame and 5s for CO2.
"""

import threading
import time
from dataclasses import dataclass, field

# Per-fixture operational limits, seconds.
MAX_BURST = {"flame": 3.0, "co2": 4.0}
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
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def arm(self) -> bool:
        """The web half of two-step arming. Refused in rehearsal mode."""
        if not self.enabled_by_config:
            return False
        with self._lock:
            self.pyro_enabled = True
        return True

    def disarm(self) -> None:
        with self._lock:
            self.pyro_enabled = False
            self._permits.clear()

    def revoke_channels(self, channels: set[int]) -> None:
        """Cancel output without shortening the reserved cooldown window."""
        with self._lock:
            for permit in self._permits:
                permit.channels.difference_update(channels)

    def gap_remaining(self, kind: str, now: float | None = None) -> float:
        """Seconds until the next burst of `kind` may be granted (0 = ready)."""
        now = time.monotonic() if now is None else now
        with self._lock:
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
        with self._lock:
            return self._request_burst_locked(
                kind, channels, duration, now, arm_channels, arm_lead
            )

    def _request_burst_locked(
        self,
        kind: str,
        channels: set[int],
        duration: float,
        now: float,
        arm_channels: set[int],
        arm_lead: float,
    ) -> bool:
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
        with self._lock:
            self._permits = [p for p in self._permits if p.expires_at > now]
            permitted: set[int] = set()
            if self.pyro_enabled:
                for permit in self._permits:
                    if permit.starts_at <= now:
                        permitted |= permit.channels
        for channel in self.pyro_channels - permitted:
            frame[channel - 1] = 0
