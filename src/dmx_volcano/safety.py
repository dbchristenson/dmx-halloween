"""Safety gate — sits BELOW the cue layer; no script can bypass it.

Invariants enforced here (see DESIGN.md "Safety invariants"):
- Pyro channels are clamped to 0 unless BOTH pyro is enabled AND an
  unexpired, bounded permit covers the channel.
- Permits are time-boxed at issue; requesting one longer than the fixture's
  max burst raises rather than truncates (a cue asking for too much fire is
  a bug to surface, not accommodate).
- Minimum gaps between bursts are enforced per fixture, measured from the
  end of one trigger window to the start of the next. Bursts never overlap.
- Flame also has a rolling duty-cycle budget: total fire time inside ANY
  window of FIRE_WINDOW seconds may not exceed the budget. A burst that
  would break it is denied, never shortened. Ending a flame burst early
  (end_burst) books only the fire that actually happened, so a short tap
  doesn't reserve the whole maximum.
- A burst may open an *arm* channel ahead of the trigger channel (the H-E01A
  refuses to fire unless its safety channel is already open when the fire
  channel rises). The arm channels are permitted from the request, the
  trigger channels only from `now + arm_lead`; both permits expire together.
- A burst may be a *sequence* of fire pulses (flame balls) under one arm
  window: the arm channels hold open across the whole sequence, the trigger
  channels are permitted only inside each pulse, and the sequence is granted
  or denied as a whole — never half of it.
- Flame pulses shorter than MIN_PULSE are refused (unburnt gas), and flame
  is denied for FLAME_AFTER_CO2 seconds after any CO2 burst ends (lingering
  CO2 can stop it igniting — unburnt gas again).
- Disarming (or restart — arming state is never persisted) drops all permits.
- The config pyro_enabled flag is a rehearsal interlock enforced here, not in
  the web layer: arm() refuses while it is off.
- Thread-safe: the web thread arms/disarms while the frame loop requests and
  clamps. One lock serializes them, so a disarm can never be undone by a
  clamp() that was mid-way through rebuilding the permit list.

Show activation limits: flame <= 10s, CO2 <= 4s.
Flame: at most 10s of fire in any 13s window; pulses >= 0.15s and >= 0.25s
apart; none within 60s after CO2.
CO2: minimum 5s gap (MOKA MK-C09 product page: "two jet time intervals
cannot be short by 5 seconds").
"""

import threading
import time
from dataclasses import dataclass, field

# Per-fixture operational limits, seconds.
MAX_BURST = {"flame": 10.0, "co2": 4.0}
# Flame's gap applies between pulses inside a pattern as well as between
# bursts: on the 2026-10-24 bench, three 0.15s pulses with no gap left the
# third unlit (unburnt gas, below); 0.25s gaps lit every pulse.
MIN_GAP = {"flame": 0.25, "co2": 5.0}
# kind -> (window seconds, max fire seconds inside any such window).
FIRE_WINDOW = {"flame": (13.0, 10.0)}
# Shortest pulse the gate grants. A pulse shorter than the H-E01A reliably
# ignites releases unburnt LPG, which is heavier than air and pools in the
# crater, and the next pulse lights the whole cloud. Bench 2026-10-24: 0.1s
# lit but weakly, 0.15s lit cleanly every time.
MIN_PULSE = {"flame": 0.15, "co2": 0.0}
# CO2 leaves the cannon cold and ~1.5x heavier than air: after the visible
# plume clears it still pools in the crater bowl around the flame head, where
# it can stop the flame from igniting. No published dissipation figure exists,
# so this is a deliberately long margin; the show never comes near it
# (dormant lasts >= 120s after every eruption).
FLAME_AFTER_CO2 = 60.0
# gap_remaining reports "ready" once at least this much fire is grantable.
READY_FIRE = 1.0
_EPS = 1e-9


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
    # kind -> booked fire intervals [start, end], oldest first (windowed kinds)
    _fired: dict[str, list[list[float]]] = field(default_factory=dict)
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    def arm(self) -> bool:
        """The web half of two-step arming. Refused in rehearsal mode."""
        if not self.enabled_by_config:
            return False
        with self._lock:
            self.pyro_enabled = True
        return True

    def disarm(self) -> None:
        # Fire history is kept: re-arming must not reset the duty-cycle budget.
        with self._lock:
            self.pyro_enabled = False
            self._permits.clear()

    def end_burst(self, kind: str, channels: set[int], now: float) -> None:
        """Stop a burst early. Windowed kinds book only the fire that actually
        happened; others keep their reserved cooldown window."""
        with self._lock:
            for permit in self._permits:
                permit.channels.difference_update(channels)
            fired = self._fired.get(kind)
            if fired and fired[-1][1] > now:
                fired[-1][1] = max(fired[-1][0], now)
                self._last_burst_end[kind] = fired[-1][1]

    def fire_budget(self, kind: str, fire_start: float) -> float:
        """Longest burst of `kind` that could start firing at `fire_start`."""
        with self._lock:
            return self._budget_locked(kind, fire_start)

    def gap_remaining(self, kind: str, now: float | None = None) -> float:
        """Seconds until a burst of `kind` may be granted (0 = ready). For
        windowed kinds, "ready" means at least READY_FIRE seconds of fire."""
        now = time.monotonic() if now is None else now
        with self._lock:
            ready = max(now, self._earliest_locked(kind))
            if kind not in FIRE_WINDOW:
                return ready - now
            want = min(READY_FIRE, MAX_BURST[kind])
            if self._fits_locked(kind, ready, want):
                return ready - now
            # Budget only grows with time once past fire is behind us; it is
            # fully restored a whole window after the last fire ends.
            lo, hi = ready, ready + FIRE_WINDOW[kind][0]
            for _ in range(50):
                mid = (lo + hi) / 2
                lo, hi = (lo, mid) if self._fits_locked(kind, mid, want) else (mid, hi)
            return hi - now

    def _earliest_locked(self, kind: str) -> float:
        """Earliest fire start allowed by the gap rules (MIN_GAP, CO2 lockout)."""
        earliest = self._last_burst_end.get(kind, float("-inf")) + MIN_GAP[kind]
        if kind == "flame":
            earliest = max(
                earliest, self._last_burst_end.get("co2", float("-inf")) + FLAME_AFTER_CO2
            )
        return earliest

    def _fire_since(self, kind: str, t: float) -> float:
        return sum(max(0.0, end - max(start, t)) for start, end in self._fired.get(kind, ()))

    def _fits_locked(self, kind: str, fire_start: float, duration: float) -> bool:
        """Would [fire_start, fire_start+duration) keep every window in budget?

        Past fire never overlaps the new burst, so the worst window is the
        one ending exactly when it ends: sliding it earlier trades new-burst
        seconds for at most as many past seconds."""
        if kind not in FIRE_WINDOW:
            return True
        window, budget = FIRE_WINDOW[kind]
        end = fire_start + duration
        return self._fire_since(kind, end - window) + duration <= budget + _EPS

    def _budget_locked(self, kind: str, fire_start: float) -> float:
        if fire_start < self._earliest_locked(kind):
            return 0.0
        if self._fits_locked(kind, fire_start, MAX_BURST[kind]):
            return MAX_BURST[kind]
        lo, hi = 0.0, MAX_BURST[kind]  # fits(d) is monotone: true then false
        for _ in range(50):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if self._fits_locked(kind, fire_start, mid) else (lo, mid)
        return lo

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
        same end. `duration` is the trigger time and is what MAX_BURST and
        FIRE_WINDOW bound; the lead only ever adds arm-channel time, never
        fire time."""
        return self.request_sequence(
            kind, channels, [(0.0, duration)], now,
            arm_channels=arm_channels, arm_lead=arm_lead,
        )

    def request_sequence(
        self,
        kind: str,
        channels: set[int],
        pulses: list[tuple[float, float]],
        now: float | None = None,
        *,
        arm_channels: set[int] = frozenset(),
        arm_lead: float = 0.0,
    ) -> bool:
        """Ask permission for a sequence of pulses under one arm window.
        Returns False if denied — the whole sequence, never part of it.

        `pulses` are (offset, duration) with offsets measured from the first
        fire, i.e. from `now + arm_lead`. Each pulse is bounded like a burst
        (MIN_PULSE..MAX_BURST) and every pulse counts toward FIRE_WINDOW; the
        arm channels hold open from now until the last pulse ends, and the
        trigger channels are permitted only inside a pulse."""
        now = time.monotonic() if now is None else now
        if not pulses:
            raise ValueError("a sequence needs at least one pulse")
        for (offset, duration), prev in zip(pulses, [None, *pulses]):
            if not MIN_PULSE[kind] - _EPS <= duration <= MAX_BURST[kind]:
                raise ValueError(
                    f"{kind} pulse of {duration}s outside "
                    f"{MIN_PULSE[kind]}-{MAX_BURST[kind]}s"
                )
            if prev is None:
                if offset < 0:
                    raise ValueError(f"{kind} pulse offsets must be >= 0")
            elif offset + _EPS < prev[0] + prev[1] + MIN_GAP[kind]:
                raise ValueError(
                    f"{kind} pulses must be in order and at least {MIN_GAP[kind]}s apart"
                )
        if arm_lead < 0:
            raise ValueError(f"arm_lead must be >= 0, got {arm_lead}")
        with self._lock:
            return self._request_locked(
                kind, channels, pulses, now, arm_channels, arm_lead
            )

    def _request_locked(
        self,
        kind: str,
        channels: set[int],
        pulses: list[tuple[float, float]],
        now: float,
        arm_channels: set[int],
        arm_lead: float,
    ) -> bool:
        if not self.pyro_enabled:
            return False
        fire_start = now + arm_lead
        windows = [(fire_start + offset, fire_start + offset + d) for offset, d in pulses]
        end = windows[-1][1]
        if fire_start < self._earliest_locked(kind):
            return False
        if kind in FIRE_WINDOW:
            fired = self._fired.setdefault(kind, [])
            kept = [f for f in fired if f[1] > fire_start - FIRE_WINDOW[kind][0]]
            # Book pulse by pulse so later pulses see the earlier ones.
            fired[:] = kept
            for start, stop in windows:
                if not self._fits_locked(kind, start, stop - start):
                    fired[:] = kept
                    return False
                fired.append([start, stop])
        if arm_channels:
            self._permits.append(
                Permit(channels=set(arm_channels), starts_at=now, expires_at=end)
            )
        for start, stop in windows:
            self._permits.append(
                Permit(channels=set(channels), starts_at=start, expires_at=stop)
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
