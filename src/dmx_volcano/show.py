"""Entry point: the 40Hz frame loop.

Every frame, in order: state machine tick -> desired channel values ->
safety clamp -> driver. The clamp is last so nothing upstream can sneak a
pyro value onto the wire.

    uv run dmx-volcano                # sim mode (fake driver)
    uv run dmx-volcano --real PORT    # real Enttec on serial PORT
"""

import argparse
import time
import tomllib
from pathlib import Path

import numpy as np

from .cues import ERUPTION, CueRunner
from .driver.fake import FakeDriver
from .driver.universe import Universe
from .events import EventLog
from .fixtures import load_rig
from .phases import Phase, PhaseConfig, PhaseMachine
from .safety import SafetyGate
from .stats import DwellConfig

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "show.toml"


def build_phase_config(raw: dict) -> PhaseConfig:
    p = raw["phases"]
    return PhaseConfig(
        dwell={
            Phase(name): DwellConfig(**params) for name, params in p["dwell"].items()
        },
        p_erupt=p["p_erupt"],
        eruption_duration=p["eruption_duration"],
        cooldown=p["cooldown"],
    )


def render(phase: Phase, rig, universe: Universe, now: float) -> None:
    """Translate the current phase into channel values. Placeholder looks:
    dormant = dim red glow, active = fog + warmer glow, eruption via cues."""
    universe.blackout()
    breathe = 0.5 + 0.5 * np.sin(now * 0.8)
    for name, fixture in rig.fixtures.items():
        if not name.startswith("par"):
            continue
        if phase is Phase.DORMANT:
            universe[fixture.channel("dimmer")] = int(60 + 40 * breathe)
            universe[fixture.channel("red")] = 255
        elif phase is Phase.ACTIVE:
            universe[fixture.channel("dimmer")] = int(120 + 80 * breathe)
            universe[fixture.channel("red")] = 255
            universe[fixture.channel("green")] = 40
        else:  # ERUPTION — cue-driven; strobe placeholder
            universe[fixture.channel("dimmer")] = 255 if int(now * 8) % 2 else 40
            universe[fixture.channel("red")] = 255
            universe[fixture.channel("green")] = 60
    if phase is not Phase.DORMANT:
        universe[rig["fogger"].channel("fog")] = 180 if phase is Phase.ACTIVE else 255


def main() -> None:
    parser = argparse.ArgumentParser(description="DMX volcano show engine")
    parser.add_argument("--real", metavar="PORT", help="serial port of the Enttec Pro")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    args = parser.parse_args()

    with open(args.config, "rb") as f:
        raw = tomllib.load(f)

    rig = load_rig(args.config)
    gate = SafetyGate(pyro_channels=rig.pyro_channels())  # starts disarmed, always
    log = EventLog(Path(raw["show"]["log_dir"]))
    machine = PhaseMachine(
        config=build_phase_config(raw),
        rng=np.random.default_rng(),
        on_transition=lambda t: log.write(
            "transition", frm=t.frm.value, to=t.to.value, cause=t.cause,
            sampled_dwell=t.sampled_dwell, actual_elapsed=t.actual_elapsed,
        ),
    )

    if args.real:
        from .driver.enttec import EnttecPro

        driver = EnttecPro(args.real)
    else:
        driver = FakeDriver()

    universe = Universe()
    runner = CueRunner(ERUPTION)
    frame_interval = 1.0 / raw["show"]["frame_rate"]
    log.write("startup", mode="real" if args.real else "sim", armed=False)
    machine.start(time.monotonic())

    try:
        while True:
            now = time.monotonic()
            machine.tick(now)
            # TODO: run eruption cues through the runner + safety gate.
            render(machine.phase, rig, universe, now)
            frame = bytearray(universe.as_bytes())
            gate.clamp(frame, now)
            driver.send_frame(bytes(frame))
            time.sleep(max(0.0, frame_interval - (time.monotonic() - now)))
    except KeyboardInterrupt:
        universe.blackout()
        driver.send_frame(universe.as_bytes())
        driver.close()
        log.write("shutdown")


if __name__ == "__main__":
    main()
