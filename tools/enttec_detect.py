"""Detect and interrogate the Enttec DMX USB Pro.

Goes beyond USB enumeration: the Pro answers framed requests over its
serial port, so a reply proves the widget itself is alive, not just the
FTDI bridge. Sends Get Widget Parameters (label 3) and Get Widget Serial
Number (label 10) per the ENTTEC "DMX USB Pro API" document.

    uv run python tools/enttec_detect.py            # auto-scan likely ports
    uv run python tools/enttec_detect.py /dev/enttec

Exit code 0 only if a widget replied. Sends no DMX — safe with fixtures
attached.
"""

import sys

import serial
from serial.tools import list_ports

SOM, EOM = 0x7E, 0xE7
LABEL_GET_PARAMS = 3
LABEL_GET_SERIAL = 10
FTDI_VID = 0x0403


def read_message(port: serial.Serial) -> tuple[int, bytes] | None:
    """Read one framed widget message; None on timeout/garbage."""
    while True:
        byte = port.read(1)
        if not byte:
            return None
        if byte[0] == SOM:
            break
    header = port.read(3)
    if len(header) < 3:
        return None
    label, length = header[0], header[1] | (header[2] << 8)
    data = port.read(length)
    if len(data) < length or port.read(1) != bytes([EOM]):
        return None
    return label, data


def request(port: serial.Serial, label: int, data: bytes = b"") -> bytes | None:
    port.reset_input_buffer()
    port.write(bytes([SOM, label, len(data) & 0xFF, len(data) >> 8]) + data + bytes([EOM]))
    reply = read_message(port)
    if reply is None or reply[0] != label:
        return None
    return reply[1]


def probe(device: str) -> bool:
    try:
        port = serial.Serial(device, baudrate=57600, timeout=1.0)
    except (serial.SerialException, OSError) as e:
        print(f"{device}: cannot open ({e})")
        return False
    with port:
        params = request(port, LABEL_GET_PARAMS, bytes([0, 0]))
        serial_no = request(port, LABEL_GET_SERIAL)
    if params is None or len(params) < 5:
        print(f"{device}: port opened but no widget reply (not an Enttec Pro?)")
        return False
    firmware = f"{params[1]}.{params[0]}"
    # 4-byte BCD, LSB first: e.g. 0x12345678 -> serial 78563412? No —
    # ENTTEC sends LSB first; render bytes reversed as BCD digits.
    bcd = "".join(f"{b:02x}" for b in reversed(serial_no)) if serial_no else "?"
    print(f"{device}: ENTTEC DMX USB Pro detected")
    print(f"  firmware version : {firmware}")
    print(f"  serial number    : {bcd}")
    print(f"  DMX break time   : {params[2]} (x10.67us)")
    print(f"  DMX MAB time     : {params[3]} (x10.67us)")
    print(f"  refresh rate     : {params[4]} packets/s (0 = max)")
    return True


def candidates() -> list[str]:
    found = []
    for p in list_ports.comports():
        if p.vid == FTDI_VID or "usbserial" in p.device or "ttyUSB" in p.device:
            found.append(p.device)
    return found


def main() -> int:
    ports = sys.argv[1:] or candidates()
    if not ports:
        print("no candidate serial ports found (is the Enttec plugged in?)")
        return 1
    return 0 if any(probe(p) for p in ports) else 1


if __name__ == "__main__":
    raise SystemExit(main())
