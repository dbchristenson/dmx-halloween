"""Enttec DMX USB Pro serial driver.

Protocol reference: ENTTEC "DMX USB Pro API" — messages are framed as
    0x7E, label, length LSB, length MSB, <data>, 0xE7
Label 6 = "Output Only Send DMX Packet". The DMX payload is the start code
(0x00) followed by the 512 channel slots.
"""

import serial

SOM = 0x7E
EOM = 0xE7
LABEL_SEND_DMX = 6
DMX_START_CODE = 0x00


class EnttecPro:
    def __init__(self, port: str, baudrate: int = 57600) -> None:
        # Baud rate is ignored by the Pro's FTDI chip but pyserial requires one.
        self._serial = serial.Serial(port, baudrate=baudrate)

    def send_frame(self, universe: bytes) -> None:
        payload = bytes([DMX_START_CODE]) + universe
        length = len(payload)
        packet = (
            bytes([SOM, LABEL_SEND_DMX, length & 0xFF, (length >> 8) & 0xFF])
            + payload
            + bytes([EOM])
        )
        self._serial.write(packet)

    def close(self) -> None:
        self._serial.close()
