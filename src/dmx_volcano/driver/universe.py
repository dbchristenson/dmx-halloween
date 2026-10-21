"""512-channel DMX frame buffer.

Channels are 1-indexed (1..512) to match fixture manuals and the DESIGN.md
channel maps. The wire representation is the raw 512-byte slot data (the
DMX start code is added by the driver, not stored here).
"""

DMX_CHANNELS = 512


class Universe:
    def __init__(self) -> None:
        self._data = bytearray(DMX_CHANNELS)

    def __getitem__(self, channel: int) -> int:
        return self._data[self._index(channel)]

    def __setitem__(self, channel: int, value: int) -> None:
        if not 0 <= value <= 255:
            raise ValueError(f"DMX value {value} out of range 0-255")
        self._data[self._index(channel)] = value

    @staticmethod
    def _index(channel: int) -> int:
        if not 1 <= channel <= DMX_CHANNELS:
            raise ValueError(f"DMX channel {channel} out of range 1-{DMX_CHANNELS}")
        return channel - 1

    def blackout(self) -> None:
        self._data[:] = bytes(DMX_CHANNELS)

    def as_bytes(self) -> bytes:
        return bytes(self._data)
