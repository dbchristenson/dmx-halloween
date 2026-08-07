"""FastAPI control page + visualizer endpoints (skeleton).

Serves the operator page: status, manual eruption trigger, software arm/
disarm (the second step of two-step arming), and the frame stream for the
2D top-down visualizer. Wiring this to the running show loop (shared state
across the uvicorn thread) is the next work item — endpoints are stubs
until then.
"""

from fastapi import FastAPI

app = FastAPI(title="DMX Volcano")


@app.get("/status")
def status() -> dict:
    # TODO: wire to live PhaseMachine/SafetyGate state.
    return {
        "phase": "dormant",
        "time_in_phase": 0.0,
        "armed": False,
        "eruptions_tonight": 0,
    }


@app.post("/trigger")
def trigger() -> dict:
    """Manual eruption request — routed through PhaseMachine.request_eruption,
    which applies the same cooldown/arming gates as automatic transitions."""
    # TODO: machine.request_eruption(); log operator action.
    return {"accepted": False, "reason": "not wired up yet"}


@app.post("/arm")
def arm() -> dict:
    """Software half of two-step arming. Physical switch is the other half."""
    # TODO: gate.arm(); log operator action.
    return {"armed": False, "reason": "not wired up yet"}


@app.post("/disarm")
def disarm() -> dict:
    # TODO: gate.disarm(); log operator action.
    return {"armed": False}
