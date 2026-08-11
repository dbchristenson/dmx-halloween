"""Entry point: the 40Hz frame loop.

Every frame, in order: state machine tick -> cue execution -> ambient render
-> effect overlay -> safety clamp -> driver. The clamp is last so nothing
upstream can sneak a pyro value onto the wire.

    uv run dmx-volcano                # sim mode (fake driver) + web UI
    uv run dmx-volcano --real PORT    # real Enttec on serial PORT
    open http://localhost:8000       # control page + visualizer
"""

import argparse
import threading
import time
import tomllib
from pathlib import Path

import numpy as np

from .audio import play
from .cues import ERUPTION, Cue, CueRunner
from .driver.fake import FakeDriver
from .driver.universe import Universe
from .events import EventLog
from .fixtures import Rig, load_rig
from .phases import Phase, PhaseConfig, PhaseMachine, Transition
from .safety import SafetyGate
from .state import ShowState
from .stats import DwellConfig

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "show.toml"
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
FLAME_SAFETY_ARM_VALUE = 130  # H-E01A arm window is 102-153; confirm from manual


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


class EffectOverlay:
    """Time-boxed channel values written by cues, applied over the ambient
    render each frame. Expiry here is cosmetic bookkeeping — the SafetyGate's
    clamp is what actually guarantees pyro shutoff."""

    def __init__(self) -> None:
        self._active: dict[int, tuple[int, float]] = {}  # channel -> (value, until)

    def set(self, channel: int, value: int, until: float) -> None:
        self._active[channel] = (value, until)

    def apply(self, universe: Universe, now: float) -> None:
        expired = [ch for ch, (_, until) in self._active.items() if until <= now]
        for ch in expired:
            del self._active[ch]
        for ch, (value, _) in self._active.items():
            universe[ch] = value


def render(phase: Phase, rig: Rig, universe: Universe, now: float) -> None:
    """Ambient look per phase: dormant = breathing red glow, active = warmer
    glow + fog, eruption = strobing lava. Pyro is never written here — only
    cues (via the overlay and the gate) touch flame/CO2."""
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
        else:  # ERUPTION
            universe[fixture.channel("dimmer")] = 255 if int(now * 8) % 2 else 40
            universe[fixture.channel("red")] = 255
            universe[fixture.channel("green")] = 60
    if phase is Phase.ACTIVE:
        universe[rig["fogger"].channel("fog")] = 180


class ShowEngine:
    """One step() per frame; owns everything between the clock and the wire."""

    def __init__(
        self,
        rig: Rig,
        gate: SafetyGate,
        machine: PhaseMachine,
        driver,
        log: EventLog,
        state: ShowState | None = None,
        timeline: list[Cue] = ERUPTION,
    ) -> None:
        self.rig = rig
        self.gate = gate
        self.machine = machine
        self.driver = driver
        self.log = log
        self.state = state
        self.universe = Universe()
        self.overlay = EffectOverlay()
        self.runner = CueRunner(timeline)
        self._prev_phase = machine.phase
        machine.on_transition = self._on_transition

    def _on_transition(self, t: Transition) -> None:
        self.log.write(
            "transition", frm=t.frm.value, to=t.to.value, cause=t.cause,
            sampled_dwell=t.sampled_dwell, actual_elapsed=t.actual_elapsed,
        )

    def step(self, now: float) -> bytes:
        self.machine.tick(now)

        if self.machine.phase is Phase.ERUPTION and self._prev_phase is not Phase.ERUPTION:
            self.runner.start(now)
            if self.state is not None:
                self.state.eruption_count += 1
            self.log.write("eruption_start", armed=self.gate.pyro_enabled)
        self._prev_phase = self.machine.phase

        if self.machine.phase is Phase.ERUPTION:
            for cue in self.runner.due_cues(now):
                self._execute(cue, now)

        render(self.machine.phase, self.rig, self.universe, now)
        self.overlay.apply(self.universe, now)
        frame = bytearray(self.universe.as_bytes())
        self.gate.clamp(frame, now)
        wire = bytes(frame)
        self.driver.send_frame(wire)

        if self.state is not None:
            with self.state.lock:
                self.state.latest_frame = wire
                self.state.now = now
        return wire

    def _execute(self, cue: Cue, now: float) -> None:
        eruption_end = self.machine.entered_at + self.machine.config.eruption_duration
        granted: bool | None = None

        if cue.action == "sound":
            play(ASSETS_DIR / cue.params["file"])
        elif cue.action == "fog":
            self.overlay.set(
                self.rig["fogger"].channel("fog"), cue.params["level"], eruption_end
            )
        elif cue.action == "co2_burst":
            ch = self.rig["co2"].channel("jet")
            granted = self.gate.request_burst(
                "co2", {ch}, cue.params["duration"], now=now
            )
            if granted:
                self.overlay.set(ch, 255, now + cue.params["duration"])
        elif cue.action == "flame_burst":
            safety_ch = self.rig["flame"].channel("safety")
            fire_ch = self.rig["flame"].channel("fire")
            granted = self.gate.request_burst(
                "flame", {safety_ch, fire_ch}, cue.params["duration"], now=now
            )
            if granted:
                until = now + cue.params["duration"]
                self.overlay.set(safety_ch, FLAME_SAFETY_ARM_VALUE, until)
                self.overlay.set(fire_ch, 255, until)
        # "pars"/"fade" are handled by the phase render for now.

        self.log.write("cue", t=cue.t, action=cue.action, granted=granted, **cue.params)


def build_engine(
    config_path: Path, driver, with_state: bool = True
) -> tuple[ShowEngine, ShowState | None, dict]:
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    rig = load_rig(config_path)
    gate = SafetyGate(pyro_channels=rig.pyro_channels())  # starts disarmed, always
    log = EventLog(Path(raw["show"]["log_dir"]))
    machine = PhaseMachine(config=build_phase_config(raw), rng=np.random.default_rng())
    state = ShowState(rig=rig, gate=gate, machine=machine, log=log) if with_state else None
    engine = ShowEngine(rig, gate, machine, driver, log, state)
    return engine, state, raw


def main() -> None:
    parser = argparse.ArgumentParser(description="DMX volcano show engine")
    parser.add_argument("--real", metavar="PORT", help="serial port of the Enttec Pro")
    parser.add_argument("--config", type=Path, default=CONFIG_PATH)
    parser.add_argument("--port", type=int, default=8000, help="web UI port")
    parser.add_argument("--no-web", action="store_true")
    args = parser.parse_args()

    if args.real:
        from .driver.enttec import EnttecPro

        driver = EnttecPro(args.real)
    else:
        driver = FakeDriver()

    engine, state, raw = build_engine(args.config, driver)
    engine.log.write("startup", mode="real" if args.real else "sim", armed=False)

    if not args.no_web:
        import uvicorn

        from .web.app import create_app

        server = uvicorn.Server(
            uvicorn.Config(
                create_app(state), host="0.0.0.0", port=args.port, log_level="warning"
            )
        )
        threading.Thread(target=server.run, daemon=True).start()

    frame_interval = 1.0 / raw["show"]["frame_rate"]
    engine.machine.start(time.monotonic())
    try:
        while True:
            now = time.monotonic()
            engine.step(now)
            time.sleep(max(0.0, frame_interval - (time.monotonic() - now)))
    except KeyboardInterrupt:
        engine.universe.blackout()
        driver.send_frame(engine.universe.as_bytes())
        driver.close()
        engine.log.write("shutdown")


if __name__ == "__main__":
    main()
