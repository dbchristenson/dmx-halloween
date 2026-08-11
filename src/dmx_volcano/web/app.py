"""Operator control page + 2D visualizer.

The web thread never touches the wire: it reads a copy of the latest frame
under the lock and submits *requests* (trigger/arm/disarm) that the show
loop and SafetyGate adjudicate. Arming here is only the software half of
two-step arming — the physical switch is the other half.
"""

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse

from ..state import ShowState

INDEX_PATH = Path(__file__).parent / "static" / "index.html"


def create_app(state: ShowState) -> FastAPI:
    app = FastAPI(title="DMX Volcano")

    @app.get("/", response_class=HTMLResponse)
    def index() -> str:
        return INDEX_PATH.read_text()

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
            ]
        }

    @app.get("/api/state")
    def show_state() -> dict:
        with state.lock:
            frame = list(state.latest_frame)
            now = state.now
        machine = state.machine
        co2 = state.co2
        return {
            "phase": machine.phase.value,
            "time_in_phase": max(0.0, now - machine.entered_at),
            "sampled_dwell": machine.dwell,
            "cooldown_remaining": max(0.0, machine.cooldown_until - now),
            "armed": state.gate.pyro_enabled,
            "eruption_count": state.eruption_count,
            "co2": {
                "remaining_kg": co2.remaining_kg,
                "remaining_fraction": co2.remaining_fraction,
                "alerted": co2.alerted,
            }
            if co2
            else None,
            "frame": frame,
        }

    @app.post("/api/trigger")
    def trigger() -> dict:
        state.machine.request_eruption()
        state.log.write("operator", action="trigger")
        return {"requested": True}

    @app.post("/api/arm")
    def arm() -> dict:
        state.gate.arm()
        state.log.write("operator", action="arm")
        return {"armed": True}

    @app.post("/api/disarm")
    def disarm() -> dict:
        state.gate.disarm()
        state.log.write("operator", action="disarm")
        return {"armed": False}

    @app.post("/api/tank/reset")
    def tank_reset() -> dict:
        """Press after physically connecting a fresh CO2 tank."""
        if state.co2 is None:
            return {"reset": False, "reason": "co2 tracking not configured"}
        state.co2.reset_tank()
        state.log.write("operator", action="tank_reset")
        return {"reset": True, "remaining_kg": state.co2.remaining_kg}

    return app
