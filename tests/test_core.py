"""Universe, stats, fixtures, and the Enttec wire format (against a fake port)."""

from pathlib import Path

import numpy as np
import pytest

from dmx_volcano.driver.universe import Universe
from dmx_volcano.fixtures import load_rig
from dmx_volcano.stats import DwellConfig, sample_dwell

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"


def test_universe_is_1_indexed_and_bounded():
    u = Universe()
    u[1] = 255
    u[512] = 7
    assert u.as_bytes()[0] == 255
    assert u.as_bytes()[511] == 7
    for bad in (0, 513):
        with pytest.raises(ValueError):
            u[bad] = 1
    with pytest.raises(ValueError):
        u[1] = 256


def test_universe_blackout():
    u = Universe()
    u[42] = 200
    u.blackout()
    assert u.as_bytes() == bytes(512)


def test_dwell_samples_respect_bounds():
    cfg = DwellConfig(t_min=30, t_max=120, k=2.0, scale=60)
    rng = np.random.default_rng(1)
    samples = [sample_dwell(cfg, rng) for _ in range(2000)]
    assert min(samples) >= cfg.t_min
    assert max(samples) <= cfg.t_max
    assert len(set(samples)) > 100  # actually stochastic, not stuck at a clip edge


def test_rig_loads_and_flags_pyro():
    rig = load_rig(CONFIG)
    assert rig["flame"].pyro and rig["co2"].pyro
    assert not rig["fogger"].pyro
    assert rig["flame"].channel("safety") == 1
    assert rig["flame"].channel("fire") == 2
    assert rig.pyro_channels() == {1, 2, 10}


def test_rig_addresses_do_not_overlap():
    rig = load_rig(CONFIG)
    used: set[int] = set()
    for fixture in rig.fixtures.values():
        spans = {fixture.channel(ch) for ch in fixture.channels}
        assert not (spans & used), f"{fixture.name} overlaps another fixture"
        used |= spans


def test_enttec_wire_format():
    from dmx_volcano.driver import enttec

    class FakePort:
        def __init__(self):
            self.written = b""

        def write(self, data):
            self.written += data

    driver = enttec.EnttecPro.__new__(enttec.EnttecPro)
    driver._serial = FakePort()
    universe = bytes(512)
    driver.send_frame(universe)
    packet = driver._serial.written
    assert packet[0] == 0x7E
    assert packet[1] == 6  # send-DMX label
    assert packet[2] | (packet[3] << 8) == 513  # start code + 512 slots
    assert packet[4] == 0x00  # DMX start code
    assert packet[-1] == 0xE7
    assert len(packet) == 4 + 513 + 1
