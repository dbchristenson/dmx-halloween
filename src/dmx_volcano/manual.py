"""Manual fixture-test console: bench-check every device from the web page.

Lets an operator turn individual PARs on/off, set the fogger level, and
test-fire CO2/flame — without running a show. Two rules keep this inside the
safety design (DESIGN.md invariants):

- Non-pyro channels (lights, fog) are sticky overrides: set, stay, clear.
  Pyro channels are REFUSED here outright — set_channels raises.
- Pyro test fires are time-boxed burst *requests*, identical to eruption
  cues: the frame loop passes them to SafetyGate.request_burst, which
  enforces arming, max duration, and min gaps. There is deliberately no
  "flame on" toggle — activation is always bounded.

Threading: the web thread writes requests under the lock; the frame loop is
the only consumer and the only thing that touches the wire.
"""

import threading
from dataclasses import dataclass, field

from .safety import MAX_BURST


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

    def enable(self) -> None:
        self.enabled = True

    def disable(self) -> None:
        """Exit test mode; every override drops so the ambient render owns
        the universe again on the next frame."""
        with self._lock:
            self.enabled = False
            self._channels.clear()
            self._burst_queue.clear()

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
            self._burst_queue.append((kind, duration))

    def pop_bursts(self) -> list[tuple[str, float]]:
        with self._lock:
            due, self._burst_queue = self._burst_queue, []
            return due

    def overrides(self) -> dict[int, int]:
        with self._lock:
            return dict(self._channels)
