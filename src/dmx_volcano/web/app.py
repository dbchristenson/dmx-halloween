"""Operator control page with top-down and Pro 3D visualizers.

The web thread never touches the wire: it reads a copy of the latest frame
under the lock and submits *requests* (trigger/arm/hold/pause/test) that the
show loop and SafetyGate adjudicate. Arming here is only the software half
of two-step arming — the physical switch is the other half — and it is
additionally refused while the config pyro_enabled flag is off (rehearsal).

Failure style: control endpoints return {"ok": false, "reason": ...} rather
than HTTP errors — the page shows the reason instead of breaking polling.
"""

from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from ..audio import list_assets, play, resolve_asset, safe_asset_name
from ..phases import Phase
from ..safety import MAX_BURST, MIN_GAP
from ..state import ShowState

INDEX_PATH = Path(__file__).parent / "static" / "index.html"
CO2CAL_PATH = Path(__file__).parent / "static" / "co2cal.html"

MAX_AUDIO_UPLOAD = 50 * 1024 * 1024  # a show stinger, not an album

# Default look for a light toggled on in test mode: warm amber, full dimmer.
TEST_LIGHT_COLOR = (255, 140, 40)


class OnRequest(BaseModel):
    on: bool


class LightRequest(BaseModel):
    name: str
    on: bool
    color: tuple[int, int, int] = TEST_LIGHT_COLOR
    dimmer: int = 255


class FogRequest(BaseModel):
    level: int  # 0-255; 0 = off


class BurstRequest(BaseModel):
    kind: str  # "flame" | "co2"
    duration: float


class HoldBeginRequest(BaseModel):
    kind: str


class HoldTokenRequest(BaseModel):
    token: str


class SpeedRequest(BaseModel):
    speed: float


def create_app(state: ShowState) -> FastAPI:
    app = FastAPI(title="DMX Volcano")
    app.mount("/static", StaticFiles(directory=INDEX_PATH.parent), name="static")

    def refuse(reason: str) -> dict:
        return {"ok": False, "reason": reason}

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_PATH.read_text()

    @app.get("/remote-control", response_class=HTMLResponse)
    def remote_control() -> str:
        return (INDEX_PATH.parent / "remote-control.html").read_text()

    @app.get("/co2cal", response_class=HTMLResponse)
    def co2cal() -> str:
        """Unlinked utility page: CO2 draw-rate calibration (weigh-between-
        bursts). Drives only the existing test-mode/arm/burst endpoints."""
        return CO2CAL_PATH.read_text()

    @app.get("/api/layout")
    def layout() -> dict:
        return {
            "fixtures": [
                {
                    "name": f.name,
                    "position": f.position,
                    "pyro": f.pyro,
                    "channels": {name: f.channel(name) for name in f.channels},
                }
                for f in state.rig.fixtures.values()
            ],
            "limits": {"max_burst": MAX_BURST, "min_gap": MIN_GAP},
        }

    @app.get("/api/state")
    def show_state() -> dict:
        with state.lock:
            frame = list(state.latest_frame)
            now = state.now
        machine = state.machine
        co2 = state.co2
        console = state.console
        last = console.last_burst if console else None
        return {
            "phase": machine.phase.value,
            "time_in_phase": max(0.0, machine.elapsed(now)),
            "sampled_dwell": machine.dwell,
            "cooldown_remaining": max(0.0, machine.cooldown_until - now),
            "armed": state.gate.pyro_enabled,
            "rehearsal": not state.gate.enabled_by_config,
            "hold": machine.hold,
            "paused": machine.paused,
            "pause_pending": machine.pause_pending,
            "eruption_count": state.eruption_count,
            "co2": {
                "remaining_kg": co2.remaining_kg,
                "remaining_fraction": co2.remaining_fraction,
                "alerted": co2.alerted,
            }
            if co2
            else None,
            "test": {
                "enabled": console.enabled,
                "overrides": {str(ch): v for ch, v in console.overrides().items()},
                "ready_in": {
                    kind: state.gate.gap_remaining(kind, now) for kind in MAX_BURST
                },
                "last_burst": {
                    "kind": last.kind,
                    "duration": last.duration,
                    "granted": last.granted,
                    "at": last.at,
                }
                if last
                else None,
            }
            if console
            else None,
            "sim": {"speed": state.clock.speed} if state.clock else None,
            "frame": frame,
        }

    @app.post("/api/trigger")
    def trigger() -> dict:
        if state.machine.hold:
            return refuse("show is held in dormant — release hold first")
        state.machine.request_eruption()
        state.log.write("operator", action="trigger")
        return {"ok": True}

    @app.post("/api/arm")
    def arm() -> dict:
        if not state.gate.arm():
            state.log.write("operator", action="arm", granted=False)
            return refuse(
                "rehearsal mode: pyro_enabled is off in show.toml — "
                "edit config and restart to allow arming"
            )
        state.log.write("operator", action="arm", granted=True)
        return {"ok": True, "armed": True}

    @app.post("/api/disarm")
    def disarm() -> dict:
        if state.console:
            state.console.cancel_holds()
        state.gate.disarm()
        state.log.write("operator", action="disarm")
        return {"ok": True, "armed": False}

    @app.post("/api/hold")
    def hold(req: OnRequest) -> dict:
        state.machine.hold = req.on
        state.log.write("operator", action="hold", on=req.on)
        return {"ok": True, "hold": req.on}

    @app.post("/api/pause")
    def pause(req: OnRequest) -> dict:
        state.machine.request_pause(req.on)
        state.log.write("operator", action="pause", on=req.on)
        return {"ok": True}

    @app.post("/api/test/enable")
    def test_enable(req: OnRequest) -> dict:
        console = state.console
        if console is None:
            return refuse("test console not configured")
        if req.on:
            if state.machine.phase is not Phase.DORMANT:
                return refuse("test mode requires the show to be dormant")
            state.machine.hold = True  # pin dormant while testing
            console.enable()
        else:
            console.disable()  # hold stays on: releasing it is a separate act
        state.log.write("operator", action="test_mode", on=req.on)
        return {"ok": True, "enabled": console.enabled}

    @app.post("/api/test/light")
    def test_light(req: LightRequest) -> dict:
        console = state.console
        if console is None or not console.enabled:
            return refuse("enable test mode first")
        fixture = state.rig.fixtures.get(req.name)
        if fixture is None or fixture.pyro:
            return refuse(f"no testable light fixture named {req.name!r}")
        values: dict[int, int] = {}
        if "dimmer" in fixture.channels:
            values[fixture.channel("dimmer")] = req.dimmer if req.on else 0
        for ch_name, component in zip(("red", "green", "blue"), req.color):
            if ch_name in fixture.channels:
                values[fixture.channel(ch_name)] = component if req.on else 0
        console.set_channels(values)
        state.log.write("operator", action="test_light", fixture=req.name, on=req.on)
        return {"ok": True}

    @app.post("/api/test/fog")
    def test_fog(req: FogRequest) -> dict:
        console = state.console
        if console is None or not console.enabled:
            return refuse("enable test mode first")
        if not 0 <= req.level <= 255:
            return refuse("fog level must be 0-255")
        console.set_channels({state.rig["fogger"].channel("fog"): req.level})
        state.log.write("operator", action="test_fog", level=req.level)
        return {"ok": True}

    @app.post("/api/test/burst")
    def test_burst(req: BurstRequest) -> dict:
        """Time-boxed pyro test fire. There is deliberately no on/off toggle
        for flame or CO2 — every activation is a bounded burst adjudicated
        by the SafetyGate (arming, max duration, min gap), same as a cue."""
        console = state.console
        if console is None or not console.enabled:
            return refuse("enable test mode first")
        if not state.gate.pyro_enabled:
            return refuse("pyro is disarmed — arm (config + web) to test fire")
        try:
            console.request_burst(req.kind, req.duration)
        except ValueError as e:
            return refuse(str(e))
        state.log.write(
            "operator", action="test_burst", burst=req.kind, duration=req.duration
        )
        return {"ok": True, "queued": True}

    @app.post("/api/remote/begin")
    def remote_begin(req: HoldBeginRequest) -> dict:
        if not state.console or not state.console.enabled:
            return refuse("enable remote controls first")
        if not state.gate.pyro_enabled:
            return refuse("pyro is disarmed")
        if state.machine.phase is not Phase.DORMANT or not state.machine.hold:
            return refuse("remote controls require hold in dormant")
        try:
            token = state.console.begin_hold(req.kind)
        except ValueError as e:
            return refuse(str(e))
        state.log.write("operator", action="remote_begin", fixture=req.kind)
        return {"ok": True, "token": token}

    @app.post("/api/remote/renew")
    def remote_renew(req: HoldTokenRequest) -> dict:
        if not state.console or not state.gate.pyro_enabled:
            return refuse("remote controls unavailable or pyro disarmed")
        try:
            state.console.renew_hold(req.token)
        except ValueError as e:
            return refuse(str(e))
        return {"ok": True}

    @app.post("/api/remote/release")
    def remote_release(req: HoldTokenRequest) -> dict:
        if state.console:
            state.console.end_hold(req.token)
        return {"ok": True}

    @app.post("/api/sim/speed")
    def sim_speed(req: SpeedRequest) -> dict:
        if state.clock is None:
            return refuse("fast-forward is sim-only (real DMX is attached)")
        return {"ok": True, "speed": state.clock.set_speed(req.speed)}

    @app.get("/api/audio")
    def audio_info() -> dict:
        """The sound pools as configured, plus every audio file in assets/
        (flagging which are referenced by no pool — imported but unassigned)."""
        bank = state.sounds
        if bank is None:
            return refuse("audio not configured")
        referenced = bank.referenced()
        return {
            "ok": True,
            **bank.describe(),
            "assets": [
                {"file": f, "assigned": f in referenced}
                for f in list_assets(bank.assets_dir)
            ],
        }

    @app.post("/api/audio/upload")
    async def audio_upload(name: str, request: Request) -> dict:
        """Import an audio file into assets/ (raw request body). Assigning it
        to a phase/transition pool stays a config edit: [audio.phases] /
        [audio.transitions] in show.toml, then restart."""
        bank = state.sounds
        if bank is None:
            return refuse("audio not configured")
        try:
            clean = safe_asset_name(name)
        except ValueError as e:
            return refuse(str(e))
        data = await request.body()
        if not data:
            return refuse("empty upload")
        if len(data) > MAX_AUDIO_UPLOAD:
            return refuse(f"file exceeds {MAX_AUDIO_UPLOAD // (1024 * 1024)}MB limit")
        bank.assets_dir.mkdir(parents=True, exist_ok=True)
        target = bank.assets_dir / clean
        replaced = target.exists()
        target.write_bytes(data)
        state.log.write(
            "operator", action="audio_import", file=clean,
            bytes=len(data), replaced=replaced,
        )
        return {
            "ok": True,
            "file": clean,
            "replaced": replaced,
            "assigned": clean in bank.referenced(),
        }

    @app.post("/api/audio/preview")
    def audio_preview(name: str) -> dict:
        """Line-check: play one asset out the real speaker through the exact
        production path (same player, same ALSA route as the show)."""
        bank = state.sounds
        if bank is None:
            return refuse("audio not configured")
        try:
            path = resolve_asset(bank.assets_dir, name)
        except ValueError as e:
            return refuse(str(e))
        if not path.exists():
            return refuse(f"{name} is not in assets/")
        if play(path) is None:
            return refuse("no audio player found (afplay/aplay/mpg123)")
        state.log.write("operator", action="audio_preview", file=name)
        return {"ok": True, "file": name}

    @app.post("/api/tank/reset")
    def tank_reset() -> dict:
        """Press after physically connecting a fresh CO2 tank."""
        if state.co2 is None:
            return refuse("co2 tracking not configured")
        state.co2.reset_tank()
        state.log.write("operator", action="tank_reset")
        return {"ok": True, "remaining_kg": state.co2.remaining_kg}

    return app
