"""Entry point: the 40Hz frame loop.

Every frame, in order: state machine tick -> cue execution -> ambient render
-> effect overlay -> safety clamp -> driver. The clamp is last so nothing
upstream can sneak a pyro value onto the wire.

    uv run dmx-volcano                # sim mode (fake driver) + web UI
    uv run dmx-volcano --real PORT    # real Enttec on serial PORT
    open http://localhost:8000       # control page + visualizer

Show content (sounds, lighting looks, cue timings, dwell distributions)
lives in config/show.toml — see docs/SHOW_AUTHORING.md.
"""

import argparse
import signal
import subprocess
import threading
import time
import tomllib
from pathlib import Path

import numpy as np

from .audio import play, stop
from .co2 import CO2Tracker
from .cues import ERUPTION, Cue, CueRunner, load_timeline
from .driver.fake import FakeDriver
from .driver.universe import Universe
from .events import EventLog
from .fixtures import Rig, load_rig
from .manual import BurstResult, TestConsole
from .notify import SmsConfig, send_sms_async, sms_config_from
from .phases import Phase, PhaseConfig, PhaseMachine, Transition
from .safety import SafetyGate
from .state import ShowState
from .stats import DwellConfig

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "show.toml"
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
FLAME_SAFETY_ARM_VALUE = 130  # H-E01A arm window is 102-153; confirm from manual

# Per-phase lighting parameters; [looks.<phase>] in show.toml overrides keys.
DEFAULT_LOOKS = {
    "dormant": {"color": [255, 0, 0], "dimmer": [60, 100], "pulse_hz": 0.13, "fog": 0},
    "active": {"color": [255, 40, 0], "dimmer": [120, 200], "pulse_hz": 0.13, "fog": 180},
    # Imminent is the attention signal: hard synchronized heartbeat that
    # accelerates over the phase, unmistakably different from the breathing.
    "imminent": {"color": [255, 190, 80], "dimmer": [0, 255], "beat_hz": [0.6, 2.5], "fog": 255},
    "eruption": {"color": [255, 60, 0], "dimmer": [40, 255], "strobe_hz": 8.0},
}


def build_phase_config(raw: dict) -> PhaseConfig:
    p = raw["phases"]
    return PhaseConfig(
        dwell={
            Phase(name): DwellConfig(**params) for name, params in p["dwell"].items()
        },
        p_erupt=p["p_erupt"],
        imminent_duration=p.get("imminent_duration", 30.0),
        eruption_duration=p["eruption_duration"],
        cooldown=p["cooldown"],
    )


def build_looks(raw: dict) -> dict:
    looks = {phase: dict(params) for phase, params in DEFAULT_LOOKS.items()}
    for phase, overrides in raw.get("looks", {}).items():
        looks.setdefault(phase, {}).update(overrides)
    return looks


class SimClock:
    """Virtual show clock, sim mode only: fast-forward validates dwell
    distributions in minutes instead of hours. The frame loop still paces at
    real 40Hz; each frame just advances show time `speed` times faster.
    Real (Enttec) mode never constructs one of these."""

    MIN_SPEED, MAX_SPEED = 0.1, 100.0

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._speed = 1.0
        self._v0 = 0.0
        self._r0 = time.monotonic()

    @property
    def speed(self) -> float:
        with self._lock:
            return self._speed

    def set_speed(self, speed: float) -> float:
        speed = min(self.MAX_SPEED, max(self.MIN_SPEED, speed))
        with self._lock:
            real = time.monotonic()
            self._v0 += (real - self._r0) * self._speed
            self._r0 = real
            self._speed = speed
        return speed

    def now(self) -> float:
        with self._lock:
            return self._v0 + (time.monotonic() - self._r0) * self._speed


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


def render(
    phase: Phase,
    rig: Rig,
    universe: Universe,
    now: float,
    progress: float,
    looks: dict,
) -> None:
    """Ambient look per phase, parameterized by config. `progress` is the
    0-1 fraction through fixed-length phases (imminent), 0 elsewhere. Pyro is
    never written here — only cues (via overlay + gate) touch flame/CO2."""
    universe.blackout()
    look = looks[phase.value]
    lo, hi = look["dimmer"]
    r, g, b = look["color"]

    if phase is Phase.IMMINENT:
        f0, f1 = look["beat_hz"]
        duration = look.get("duration", 30.0)  # injected from imminent_duration
        # Linear chirp accelerating f0 -> f1: angle(t) = 2pi*(f0*t + (f1-f0)*t^2/2D)
        angle = 2 * np.pi * duration * (f0 * progress + (f1 - f0) * progress**2 / 2)
        level = max(0.0, np.sin(angle)) ** 6  # sharp beats, dark gaps
    elif phase is Phase.ERUPTION:
        level = 1.0 if int(now * look["strobe_hz"]) % 2 else 0.15
    else:
        level = 0.5 + 0.5 * np.sin(now * 2 * np.pi * look["pulse_hz"])

    dimmer = int(lo + (hi - lo) * level)
    for name, fixture in rig.fixtures.items():
        if not name.startswith("par"):
            continue
        universe[fixture.channel("dimmer")] = dimmer
        universe[fixture.channel("red")] = r
        universe[fixture.channel("green")] = g
        universe[fixture.channel("blue")] = b

    fog = look.get("fog", 0)
    if fog:
        universe[rig["fogger"].channel("fog")] = fog


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
        timeline: list[Cue] | None = None,
        looks: dict | None = None,
        phase_sounds: dict[str, str] | None = None,
        co2: CO2Tracker | None = None,
        sms: SmsConfig | None = None,
        console: TestConsole | None = None,
    ) -> None:
        self.rig = rig
        self.gate = gate
        self.console = console
        self.machine = machine
        self.driver = driver
        self.log = log
        self.state = state
        self.co2 = co2
        self.sms = sms or SmsConfig()
        self.looks = looks or build_looks({})
        self.looks["imminent"].setdefault("duration", machine.config.imminent_duration)
        self.phase_sounds = phase_sounds or {}
        self.universe = Universe()
        self.overlay = EffectOverlay()
        self.runner = CueRunner(timeline if timeline is not None else ERUPTION)
        self._prev_phase = machine.phase
        self._ambient_proc: subprocess.Popen | None = None
        machine.on_transition = self._on_transition

    def _on_transition(self, t: Transition) -> None:
        self.log.write(
            "transition", frm=t.frm.value, to=t.to.value, cause=t.cause,
            sampled_dwell=t.sampled_dwell, actual_elapsed=t.actual_elapsed,
        )
        stop(self._ambient_proc)
        sound = self.phase_sounds.get(t.to.value)
        self._ambient_proc = play(ASSETS_DIR / sound) if sound else None

    def step(self, now: float) -> bytes:
        self.machine.tick(now)
        phase = self.machine.phase

        if phase is Phase.ERUPTION and self._prev_phase is not Phase.ERUPTION:
            self.runner.start(now)
            if self.state is not None:
                self.state.eruption_count += 1
            self.log.write("eruption_start", armed=self.gate.pyro_enabled)
        self._prev_phase = phase

        if phase is Phase.ERUPTION:
            for cue in self.runner.due_cues(now):
                self._execute(cue, now)

        if self.console is not None and self.console.enabled:
            # Test mode: the operator owns the universe. Blackout base so a
            # fixture lights up if and only if its toggle is on; pyro test
            # fires run through the exact same gate path as eruption cues.
            self.universe.blackout()
            for ch, value in self.console.overrides().items():
                self.universe[ch] = value
            for kind, duration in self.console.pop_bursts():
                granted = self._pyro_burst(kind, duration, now)
                self.console.last_burst = BurstResult(kind, duration, granted, now)
                self.log.write("test_burst", burst=kind, duration=duration, granted=granted)
        else:
            progress = 0.0
            if phase is Phase.IMMINENT:
                progress = min(
                    1.0, (now - self.machine.entered_at) / self.machine.config.imminent_duration
                )
            render(phase, self.rig, self.universe, now, progress, self.looks)
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
            granted = self._pyro_burst("co2", cue.params["duration"], now)
        elif cue.action == "flame_burst":
            granted = self._pyro_burst("flame", cue.params["duration"], now)
        # "pars"/"fade" are handled by the phase render for now.

        self.log.write("cue", t=cue.t, action=cue.action, granted=granted, **cue.params)

    def _pyro_burst(self, kind: str, duration: float, now: float) -> bool:
        """The one path that puts pyro on the wire — used by eruption cues
        and manual test fires alike, always via a SafetyGate permit."""
        if kind == "co2":
            ch = self.rig["co2"].channel("jet")
            granted = self.gate.request_burst("co2", {ch}, duration, now=now)
            if granted:
                self.overlay.set(ch, 255, now + duration)
                self._track_co2(duration)
        else:
            safety_ch = self.rig["flame"].channel("safety")
            fire_ch = self.rig["flame"].channel("fire")
            granted = self.gate.request_burst(
                "flame", {safety_ch, fire_ch}, duration, now=now
            )
            if granted:
                until = now + duration
                self.overlay.set(safety_ch, FLAME_SAFETY_ARM_VALUE, until)
                self.overlay.set(fire_ch, 255, until)
        return granted

    def _track_co2(self, duration: float) -> None:
        if self.co2 is None:
            return
        if self.co2.record_burst(duration):
            message = (
                f"VOLCANO: CO2 tank low — {self.co2.remaining_kg:.1f}kg "
                f"({self.co2.remaining_fraction:.0%}) remaining. Swap the tank "
                f"and press 'Tank reset' on the control page."
            )
            self.log.write(
                "alert", alert="co2_low", remaining_kg=self.co2.remaining_kg
            )
            send_sms_async(self.sms, message)


def build_engine(
    config_path: Path, driver, with_state: bool = True
) -> tuple[ShowEngine, ShowState | None, dict]:
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    rig = load_rig(config_path)
    # Starts disarmed, always; web arming additionally requires the config
    # pyro_enabled flag (rehearsal interlock).
    gate = SafetyGate(
        pyro_channels=rig.pyro_channels(),
        enabled_by_config=raw["show"].get("pyro_enabled", False),
    )
    console = TestConsole(pyro_channels=rig.pyro_channels())
    log = EventLog(Path(raw["show"]["log_dir"]))
    machine = PhaseMachine(config=build_phase_config(raw), rng=np.random.default_rng())

    co2_cfg = raw.get("co2")
    co2 = (
        CO2Tracker(
            capacity_kg=co2_cfg["capacity_kg"],
            rate_kg_per_s=co2_cfg["rate_kg_per_s"],
            warn_remaining_fraction=co2_cfg.get("warn_remaining_fraction", 0.15),
            state_path=Path(co2_cfg.get("state_file", "var/co2_tank.json")),
        )
        if co2_cfg
        else None
    )

    timeline_cfg = raw.get("cues", {}).get("eruption")
    timeline = load_timeline(timeline_cfg) if timeline_cfg else None

    state = (
        ShowState(rig=rig, gate=gate, machine=machine, log=log, co2=co2, console=console)
        if with_state
        else None
    )
    engine = ShowEngine(
        rig, gate, machine, driver, log, state,
        timeline=timeline,
        looks=build_looks(raw),
        phase_sounds=raw.get("audio", {}),
        co2=co2,
        sms=sms_config_from(raw),
        console=console,
    )
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
    clock = None if args.real else SimClock()
    if state is not None:
        state.clock = clock
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

    # Route SIGTERM (systemd stop) through the same blackout path as Ctrl-C,
    # and re-enable SIGINT even if inherited as ignored (background shells set
    # SIG_IGN, which Python would otherwise silently keep).
    def _shutdown_signal(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, _shutdown_signal)
    signal.signal(signal.SIGINT, _shutdown_signal)

    frame_interval = 1.0 / raw["show"]["frame_rate"]
    engine.machine.start(clock.now() if clock else time.monotonic())
    try:
        while True:
            real_now = time.monotonic()
            engine.step(clock.now() if clock else real_now)
            time.sleep(max(0.0, frame_interval - (time.monotonic() - real_now)))
    except KeyboardInterrupt:
        engine.universe.blackout()
        driver.send_frame(engine.universe.as_bytes())
        driver.close()
        engine.log.write("shutdown")


if __name__ == "__main__":
    main()
