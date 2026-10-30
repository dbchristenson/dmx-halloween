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
import os
import random
import signal
import threading
import time
import tomllib
from pathlib import Path

from .audio import LayeredPlayer, SoundBank
from .co2 import CO2Tracker
from .cues import ERUPTION, Cue, CueRunner, load_timeline, validate_timeline
from .driver.fake import FakeDriver
from .driver.universe import Universe
from .events import EventLog
from .flame import FlameConfig, FlamePlanner, Shot, parse_pattern
from .fixtures import Rig, load_rig
from .lighting import build_looks, render
from .manual import BurstResult, TestConsole
from .notify import SmsConfig, send_sms_async, sms_config_from
from .phases import Phase, PhaseConfig, PhaseMachine, Transition
from .safety import READY_FIRE, SafetyGate
from .state import ShowState
from .stats import DwellConfig

CONFIG_PATH = Path(__file__).resolve().parents[2] / "config" / "show.toml"
ASSETS_DIR = Path(__file__).resolve().parents[2] / "assets"
FLAME_SAFETY_ARM_VALUE = 130  # H-E01A arm window is 102-153 (manual, 2CH mode)
# The H-E01A only fires if its safety channel is already open when the fire
# channel rises ("activate the safety channel, then use channel 2"). Raising
# both in the same frame armed nothing on the 2026-09-11 bench. So a flame
# burst or pattern opens safety this long before fire; the gate clamps fire
# until then.
FLAME_ARM_LEAD = 0.5


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
        # channel -> (value, since, until)
        self._active: dict[int, tuple[int, float, float]] = {}

    def set(
        self, channel: int, value: int, until: float, since: float = float("-inf")
    ) -> None:
        self._active[channel] = (value, since, until)

    def apply(self, universe: Universe, now: float) -> None:
        expired = [ch for ch, (_, _, until) in self._active.items() if until <= now]
        for ch in expired:
            del self._active[ch]
        for ch, (value, since, _) in self._active.items():
            if since <= now:
                universe[ch] = value


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
        sounds: SoundBank | None = None,
        co2: CO2Tracker | None = None,
        sms: SmsConfig | None = None,
        console: TestConsole | None = None,
        flame: FlameConfig | None = None,
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
        self.sounds = sounds
        self.audio = LayeredPlayer(sounds) if sounds is not None else None
        self.universe = Universe()
        self.overlay = EffectOverlay()
        self._remote_events: list[tuple[str, dict]] = []
        timeline = timeline if timeline is not None else ERUPTION
        self.runner = CueRunner(timeline)
        # The caldera blasts white over each CO2 cue, armed or not, so rehearsal
        # shows the same look as show night.
        self.co2_windows = [
            (c.t, c.t + c.params["duration"]) for c in timeline if c.action == "co2_burst"
        ]
        self.flame = FlamePlanner(
            flame or FlameConfig(),
            lead=FLAME_ARM_LEAD,
            imminent_duration=machine.config.imminent_duration,
            first_co2=min((start for start, _ in self.co2_windows), default=float("inf")),
        )
        self._moves: list[Transition] = []
        self._flame_plan: list[Shot] = []  # scheduled, not yet started
        self._flame_live: list[tuple[Shot, bool]] = []  # started: (shot, granted)
        # When the latest CO2 burst ends: the eruption's scripted last cue end
        # (so rehearsal times the aftermath like show night), pushed later by
        # any granted burst that actually ends later.
        self._co2_end = float("-inf")
        self._co2_last_cue = max((end for _, end in self.co2_windows), default=float("-inf"))
        self._prev_phase = machine.phase
        machine.on_transition = self._on_transition

    def _on_transition(self, t: Transition) -> None:
        self.log.write(
            "transition", frm=t.frm.value, to=t.to.value, cause=t.cause,
            sampled_dwell=t.sampled_dwell, actual_elapsed=t.actual_elapsed,
        )
        if self.audio is not None:
            self.audio.enter_phase(t.frm.value, t.to.value)
        # Planned in step(), which knows the time; a new move replaces any
        # pattern still waiting from the previous phase.
        self._moves.append(t)

    def stop_audio(self) -> None:
        if self.audio is not None:
            self.audio.stop_all()

    def step(self, now: float) -> bytes:
        self.machine.tick(now)
        phase = self.machine.phase
        for move in self._moves:
            self._flame_plan = self.flame.plan(
                move.frm.value, move.to.value, move.cause, now, co2_end=self._co2_end
            )
        self._moves.clear()
        if self._flame_plan and phase is Phase.DORMANT and (
            self.machine.hold or self.machine.paused
            or (self.console is not None and self.console.enabled)
        ):
            # The eruption's aftermath waits in dormant; an operator stopping
            # the show (hold, pause, test mode) cancels it.
            for shot in self._flame_plan:
                self.log.write("flame_pattern", source=shot.source, pattern=shot.pattern,
                               pulses=shot.pulses, granted=False, cancelled=True)
            self._flame_plan = []
        while self._flame_plan and self._flame_plan[0].start <= now:
            self._fire_shot(self._flame_plan.pop(0), now)
        self._flame_live = [
            (shot, granted) for shot, granted in self._flame_live
            if shot.end(FLAME_ARM_LEAD) > now
        ]
        if self.audio is not None:
            if self.audio.phase is None:
                # First frame after startup: no transition fired, so begin
                # the boot phase's layers (dormant has a bed too).
                self.audio.enter_phase(phase.value, phase.value)
            self.audio.tick(now)  # loop respawns + accent scheduling

        if phase is Phase.ERUPTION and self._prev_phase is not Phase.ERUPTION:
            self.runner.start(now)
            self._co2_end = max(self._co2_end, now + self._co2_last_cue)
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
            for pattern, timing in self.console.pop_patterns():
                shot = Shot(now, pattern, parse_pattern(pattern, timing), "test")
                granted = self._fire_shot(shot, now)
                self.console.last_burst = BurstResult("flame", 0.0, granted, now)
        else:
            render(
                phase, self.rig, self.universe, now, self.machine.elapsed(now), self.looks,
                self.co2_windows,
                # Lights flash with every scheduled pulse, armed or not, so
                # rehearsal shows the same look as show night.
                [w for shot, _ in self._flame_live for w in shot.fire_windows(FLAME_ARM_LEAD)],
            )
        self._write_flame(now)
        if self.console is not None:
            self.console.step_holds(
                now, self.gate.pyro_enabled and self.machine.phase is Phase.DORMANT
                and self.machine.hold,
                self._start_remote_hold, self._stop_remote_hold, FLAME_ARM_LEAD,
            )
        self.overlay.apply(self.universe, now)
        frame = bytearray(self.universe.as_bytes())
        self.gate.clamp(frame, now)
        wire = bytes(frame)
        self.driver.send_frame(wire)

        if self.state is not None:
            with self.state.lock:
                self.state.latest_frame = wire
                self.state.now = now
        self._flush_remote_events()
        return wire

    def _execute(self, cue: Cue, now: float) -> None:
        eruption_end = self.machine.entered_at + self.machine.config.eruption_duration
        granted: bool | None = None

        if cue.action == "sound":
            if self.audio is not None:
                self.audio.play_one_shot(self.sounds.cue_sound(cue.t))
        elif cue.action == "fog":
            self.overlay.set(
                self.rig["fogger"].channel("fog"), cue.params["level"], eruption_end
            )
        elif cue.action == "co2_burst":
            granted = self._pyro_burst("co2", cue.params["duration"], now)
        # "pars"/"fade" are handled by the phase render for now.

        self.log.write("cue", t=cue.t, action=cue.action, granted=granted, **cue.params)

    def _fire_shot(self, shot: Shot, now: float) -> bool:
        """The one path that puts a flame pattern on the wire — the show's
        transition/imminent patterns and test-console patterns alike. The
        gate grants the whole sequence or none of it."""
        shot.start = now
        granted = self.gate.request_sequence(
            "flame", {self.rig["flame"].channel("fire")}, shot.pulses, now=now,
            arm_channels={self.rig["flame"].channel("safety")}, arm_lead=FLAME_ARM_LEAD,
        )
        self._flame_live.append((shot, granted))
        self.log.write(
            "flame_pattern", source=shot.source, pattern=shot.pattern,
            pulses=shot.pulses, granted=granted,
        )
        return granted

    def _write_flame(self, now: float) -> None:
        """Granted patterns hold safety open and raise fire inside each
        pulse. The gate's permits are what actually bound it on the wire."""
        for shot, granted in self._flame_live:
            if not granted or not shot.start <= now < shot.end(FLAME_ARM_LEAD):
                continue
            self.universe[self.rig["flame"].channel("safety")] = FLAME_SAFETY_ARM_VALUE
            if any(a <= now < b for a, b in shot.fire_windows(FLAME_ARM_LEAD)):
                self.universe[self.rig["flame"].channel("fire")] = 255

    def _pyro_burst(
        self, kind: str, duration: float, now: float, *, track_co2: bool = True
    ) -> bool:
        """The one path that puts a single burst on the wire — eruption CO2
        cues and manual test fires alike, always via a SafetyGate permit."""
        if kind == "co2":
            ch = self.rig["co2"].channel("jet")
            granted = self.gate.request_burst("co2", {ch}, duration, now=now)
            if granted:
                self.overlay.set(ch, 255, now + duration)
                self._co2_end = max(self._co2_end, now + duration)
                if track_co2:
                    self._track_co2(duration)
        else:
            safety_ch = self.rig["flame"].channel("safety")
            fire_ch = self.rig["flame"].channel("fire")
            granted = self.gate.request_burst(
                "flame", {fire_ch}, duration, now=now,
                arm_channels={safety_ch}, arm_lead=FLAME_ARM_LEAD,
            )
            if granted:
                fire_start = now + FLAME_ARM_LEAD
                until = fire_start + duration
                self.overlay.set(safety_ch, FLAME_SAFETY_ARM_VALUE, until)
                self.overlay.set(fire_ch, 255, until, since=fire_start)
        return granted

    def _start_remote_hold(self, kind: str, duration: float, now: float) -> float:
        """Start a hold with as much fire as the gate's budget allows, up to
        `duration`. Returns the granted duration (0.0 = denied)."""
        lead = FLAME_ARM_LEAD if kind == "flame" else 0.0
        duration = min(duration, self.gate.fire_budget(kind, now + lead))
        granted = duration >= READY_FIRE and self._pyro_burst(
            kind, duration, now, track_co2=False
        )
        self._remote_events.append(("remote_hold_start", {"fixture": kind, "granted": granted}))
        return duration if granted else 0.0

    def _stop_remote_hold(self, kind: str, now: float, duration: float) -> None:
        fixture = self.rig[kind]
        channels = {fixture.channel(name) for name in fixture.channels}
        self.gate.end_burst(kind, channels, now)
        for channel in channels:
            self.overlay.set(channel, 0, now)
            self.universe[channel] = 0

        self._remote_events.append(("remote_hold_stop", {"fixture": kind, "elapsed": duration}))

    def _flush_remote_events(self) -> None:
        # Always called AFTER send_frame: disk writes must never delay release.
        events, self._remote_events = self._remote_events, []
        for event, data in events:
            if event == "remote_hold_stop" and data["fixture"] == "co2":
                if data["elapsed"] > 0:
                    self._track_co2(data["elapsed"])
            self.log.write(event, **data)

    def shutdown(self, now: float) -> None:
        """Blackout first, then finalize holds and their accounting.

        Runs on EVERY exit, crashes included: the Enttec keeps repeating the
        last frame it was sent, so a dead process must not leave pyro
        channels high on the wire. If the driver itself is what failed, the
        blackout can't go out — carry on so the rest still tidies up."""
        self.universe.blackout()
        try:
            self.driver.send_frame(self.universe.as_bytes())
        except Exception as error:
            print(f"ERROR: shutdown blackout frame failed: {error!r}", flush=True)
        self.gate.disarm()
        if self.console is not None:
            self.console.disable()
            self.console.step_holds(
                now, False, self._start_remote_hold, self._stop_remote_hold, FLAME_ARM_LEAD,
            )
        self._flush_remote_events()
        try:
            self.driver.close()
        except Exception as error:
            print(f"ERROR: driver close failed: {error!r}", flush=True)
        self.stop_audio()
        self.log.write("shutdown")

    def _track_co2(self, duration: float) -> None:
        if self.co2 is None:
            return
        try:
            crossed = self.co2.record_burst(duration)
        except OSError as error:
            # Usage is already counted in memory; only persistence failed.
            # A full/failed SD card must not take down the frame loop.
            print(f"WARNING: CO2 tank state write failed: {error}", flush=True)
            return
        if crossed:
            message = (
                f"VOLCANO: CO2 tank low — {self.co2.remaining_kg:.1f}kg "
                f"({self.co2.remaining_fraction:.0%}) remaining. Swap the tank "
                f"and press 'Tank reset' on the control page."
            )
            self.log.write(
                "alert", alert="co2_low", remaining_kg=self.co2.remaining_kg
            )
            send_sms_async(self.sms, message)


WEB_RETRY_S = 5.0


def serve_web(app, port: int, log: EventLog) -> None:
    """Run the control page forever, restarting it whenever uvicorn exits.

    uvicorn's run() returns (or raises SystemExit — e.g. port already in use)
    and its thread simply ends; the show would keep running with no page to
    disarm from. So loop: a fresh Server each attempt, a short pause between.
    Network drops don't need this — the socket stays bound to 0.0.0.0 and
    serves again when WiFi comes back."""
    import uvicorn

    while True:
        server = uvicorn.Server(
            uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
        )
        try:
            server.run()
            reason = "server exited"
        except BaseException as error:  # SystemExit is how uvicorn reports bind failure
            reason = repr(error)
        log.write("web_down", reason=reason, retry_in=WEB_RETRY_S)
        print(f"WARNING: control page down ({reason}); retrying in {WEB_RETRY_S:.0f}s", flush=True)
        time.sleep(WEB_RETRY_S)


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
    machine = PhaseMachine(config=build_phase_config(raw), rng=random.Random())

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
    timeline = load_timeline(timeline_cfg) if timeline_cfg else ERUPTION
    validate_timeline(timeline)
    flame = FlameConfig.from_config(raw.get("flame", {}))
    flame.check_finale(
        FLAME_ARM_LEAD,
        machine.config.imminent_duration,
        min((c.t for c in timeline if c.action == "co2_burst"), default=float("inf")),
    )

    sounds = SoundBank.from_config(raw.get("audio", {}), ASSETS_DIR)
    for cue in timeline:
        if cue.action == "sound":
            sounds.add_cue(cue.t, cue.params["file"])
    missing = sounds.missing_files()
    if missing:
        # Loud at startup, tolerated at play time: the show runs, silently.
        log.write("audio_missing", files=missing)
        print(
            f"WARNING: audio files referenced but not in assets/: {', '.join(missing)}",
            flush=True,  # stdout is block-buffered under systemd
        )

    state = (
        ShowState(
            rig=rig, gate=gate, machine=machine, log=log, co2=co2,
            console=console, sounds=sounds, flame_timing=flame.timing,
            # From the environment (.env via systemd), never config: the
            # repo is public. Unset means the web page cannot arm at all.
            arm_pin=os.environ.get("DMX_ARM_PIN") or None,
        )
        if with_state
        else None
    )
    engine = ShowEngine(
        rig, gate, machine, driver, log, state,
        timeline=timeline,
        looks=build_looks(raw),
        sounds=sounds,
        co2=co2,
        sms=sms_config_from(raw),
        console=console,
        flame=flame,
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
        # Blank the wire before anything else can fail: after a SIGKILL/OOM
        # the widget is still repeating the dead process's last frame, and a
        # bad config below would otherwise exit without ever replacing it.
        driver.send_frame(bytes(512))
    else:
        driver = FakeDriver()

    engine, state, raw = build_engine(args.config, driver)
    clock = None if args.real else SimClock()
    if state is not None:
        state.clock = clock
    engine.log.write("startup", mode="real" if args.real else "sim", armed=False)

    if not args.no_web:
        from .web.app import create_app

        threading.Thread(
            target=serve_web, args=(create_app(state), args.port, engine.log), daemon=True
        ).start()

    # Route SIGTERM (systemd stop) through the same blackout path as Ctrl-C,
    # and re-enable SIGINT even if inherited as ignored (background shells set
    # SIG_IGN, which Python would otherwise silently keep).
    def _shutdown_signal(signum, frame):
        # One stop request is enough. systemd signals the whole cgroup AND uv
        # forwards its copy, so a second SIGTERM lands mid-shutdown and used
        # to abort it before the blackout frame went out. Ignore the rest.
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        signal.signal(signal.SIGINT, signal.SIG_IGN)
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
        pass
    finally:
        # Any other exception still blacks out here, then propagates so the
        # traceback reaches the journal and systemd restarts us disarmed.
        engine.shutdown(clock.now() if clock else time.monotonic())


if __name__ == "__main__":
    main()
