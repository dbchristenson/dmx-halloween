"""Manual fixture-test console: bench-check every device from the web page.

Lets an operator turn individual PARs on/off, set the fogger level, and
test-fire CO2/flame — without running a show. Two rules keep this inside the
safety design (DESIGN.md invariants):

- Non-pyro channels (lights, fog) are sticky overrides: set, stay, clear.
  Pyro channels are REFUSED here outright — set_channels raises.
- Pyro test fires are time-boxed burst *requests*, identical to eruption
  cues: the frame loop passes them to SafetyGate.request_burst, which
  enforces arming, max duration, min gaps, and the flame duty cycle. There is deliberately no
  "flame on" toggle — activation is always bounded.

Threading: the web thread writes requests under the lock; the frame loop is
the only consumer and the only thing that touches the wire.
"""

import secrets
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from .safety import MAX_BURST

HOLD_LEASE = 0.75


@dataclass
class Hold:
    kind: str
    lease_until: float
    confirmed: bool = False
    started: bool = False
    released: bool = False
    wall_end: float = float('inf')
    show_end: float = float('inf')
    show_start: float = 0.0
    duration: float = 0.0  # fire granted at start; may be under MAX_BURST


@dataclass
class BurstResult:
    kind: str
    duration: float
    granted: bool
    at: float


@dataclass
class TestConsole:
    __test__ = False  # "Test" prefix is domain language, not a pytest class

    pyro_channels: set[int]
    enabled: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _channels: dict[int, int] = field(default_factory=dict)
    _burst_queue: list[tuple[str, float]] = field(default_factory=list)
    last_burst: BurstResult | None = None
    _holds: dict[str, Hold] = field(default_factory=dict)

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        """Exit test mode; every override drops so the ambient render owns
        the universe again on the next frame."""
        with self._lock:
            self.enabled = False
            self._channels.clear()
            self._burst_queue.clear()
            for hold in self._holds.values():
                hold.released = True

    def set_channels(self, values: dict[int, int]) -> None:
        """Sticky overrides for non-pyro channels. 0 clears the override."""
        bad = set(values) & self.pyro_channels
        if bad:
            raise ValueError(f"pyro channels {sorted(bad)} cannot be set directly")
        with self._lock:
            for ch, v in values.items():
                if not 0 <= v <= 255:
                    raise ValueError(f"DMX value {v} out of range 0-255")
                if v == 0:
                    self._channels.pop(ch, None)
                else:
                    self._channels[ch] = v

    def request_burst(self, kind: str, duration: float) -> None:
        """Queue a time-boxed pyro test fire; the frame loop adjudicates it
        through the SafetyGate. Overlong requests fail fast here so the web
        layer can report them instead of 500ing in the loop."""
        if kind not in MAX_BURST:
            raise ValueError(f"unknown burst kind {kind!r}")
        if not 0 < duration <= MAX_BURST[kind]:
            raise ValueError(
                f"{kind} burst must be 0-{MAX_BURST[kind]}s, got {duration}"
            )
        with self._lock:
            if any(h.kind == kind for h in self._holds.values()):
                raise ValueError('fixture is controlled by a remote hold')
            self._burst_queue.append((kind, duration))

    def pop_bursts(self) -> list[tuple[str, float]]:
        with self._lock:
            due, self._burst_queue = self._burst_queue, []
            return due

    def overrides(self) -> dict[int, int]:
        with self._lock:
            return dict(self._channels)

    def begin_hold(self, kind: str) -> str:
        """Reserve a hold. It produces no output until its first renewal."""
        if kind not in MAX_BURST:
            raise ValueError('unknown hold fixture')
        with self._lock:
            if not self.enabled:
                raise ValueError('enable remote controls first')
            if self._burst_queue or any(h.kind == kind for h in self._holds.values()):
                raise ValueError('fixture already has a hold; release it first')
            token = secrets.token_urlsafe(24)
            self._holds[token] = Hold(kind, time.monotonic() + HOLD_LEASE)
            return token

    def renew_hold(self, token: str) -> None:
        with self._lock:
            hold = self._holds.get(token)
            now = time.monotonic()
            if (hold is None or hold.released or not self.enabled
                    or now >= hold.lease_until or now >= hold.wall_end):
                raise ValueError('hold expired or released; release and press again')
            hold.confirmed = True
            hold.lease_until = now + HOLD_LEASE

    def end_hold(self, token: str) -> None:
        with self._lock:
            if token in self._holds:
                self._holds[token].released = True

    def cancel_holds(self) -> None:
        with self._lock:
            for hold in self._holds.values():
                hold.released = True

    def step_holds(
        self, now: float, allowed: bool,
        start: Callable[[str, float, float], float],
        stop: Callable[[str, float, float], None], arm_lead: float,
    ) -> None:
        """Frame-loop owned output; the lock serializes release versus start.

        Wall time bounds abandoned clients even at slow simulation speed.
        Show time also bounds every permit through the existing SafetyGate.
        A finished token is deleted, so delayed renewals cannot restart it.
        """
        with self._lock:
            wall = time.monotonic()
            for token, hold in list(self._holds.items()):
                expired = (hold.released or not self.enabled or not allowed
                           or wall >= hold.lease_until or wall >= hold.wall_end
                           or now >= hold.show_end)
                if expired:
                    if hold.started:
                        elapsed = min(hold.duration, max(0.0, now - hold.show_start))
                        stop(hold.kind, now, elapsed)
                    del self._holds[token]
                    continue
                if hold.confirmed and not hold.started:
                    # start() returns the fire it granted: the full maximum,
                    # or less if the gate's duty-cycle budget is part-spent.
                    duration = start(hold.kind, MAX_BURST[hold.kind], now)
                    granted = duration > 0
                    self.last_burst = BurstResult(hold.kind, duration, granted, now)
                    if not granted:
                        del self._holds[token]
                        continue
                    hold.started = True
                    hold.show_start = now
                    hold.duration = duration
                    window = duration + (arm_lead if hold.kind == 'flame' else 0)
                    hold.wall_end = wall + window
                    hold.show_end = now + window
