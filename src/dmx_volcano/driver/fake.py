"""Development driver: records frames instead of writing to a serial port.

The visualizer and tests consume `last_frame` / `on_frame` — the exact bytes
that would have hit the wire, so simulation can never diverge from reality.
"""

from collections.abc import Callable


class FakeDriver:
    def __init__(self, on_frame: Callable[[bytes], None] | None = None) -> None:
        self.last_frame: bytes | None = None
        self.frame_count = 0
        self._on_frame = on_frame

    def send_frame(self, universe: bytes) -> None:
        self.last_frame = universe
        self.frame_count += 1
        if self._on_frame is not None:
            self._on_frame(universe)

    def close(self) -> None:
        pass
