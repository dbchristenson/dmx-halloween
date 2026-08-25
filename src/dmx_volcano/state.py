"""Shared state between the show loop and the web thread.

The frame loop is the only writer of `latest_frame`; the web thread only
reads (under the lock) and pokes thread-safe request flags on the machine/
gate. Keep it that way — the wire must have exactly one owner.
"""

import threading
from dataclasses import dataclass, field
from typing import Any

from .co2 import CO2Tracker
from .events import EventLog
from .fixtures import Rig
from .manual import TestConsole
from .phases import PhaseMachine
from .safety import SafetyGate


@dataclass
class ShowState:
    rig: Rig
    gate: SafetyGate
    machine: PhaseMachine
    log: EventLog
    co2: CO2Tracker | None = None
    console: TestConsole | None = None
    clock: Any = None  # SimClock in sim mode, None on the real wire
    lock: threading.Lock = field(default_factory=threading.Lock)
    latest_frame: bytes = bytes(512)
    now: float = 0.0
    eruption_count: int = 0
