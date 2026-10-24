"""Ambient lighting: the per-phase look every light shows, frame by frame.

Design principles (keep new looks inside these):
- One dominant color sets each phase's mood; a minority of accents adds texture.
- Nothing moves in unison: every light runs its own rate, offset, and choices.
- Use the whole range: deep lows and full highs, not a polite middle band.
- Each phase is faster and hotter than the one before it.
- Phase changes are events: snap to black, then sweep in. Never a quiet fade.
- The outer pars frame the volcano and stay warm; the crater is where the
  eye goes, so the crater gets the wild color and the white blasts.

Two groups:
- Outer pars (par*): the wash on the cone.
- Caldera: the crater lights and the fogger's LEDs. Fog is often running,
  so they color the smoke in sulfur/firework chemistry colors. In the
  eruption they go white, solid through each CO2 burst, to light the ice.

Every "random" choice comes from a hash of the light's index and the beat or
episode number, never from an RNG, so a frame is a pure function of time and
the looks can be asserted on in tests.
"""

import math

from .driver.universe import Universe
from .fixtures import Rig
from .phases import Phase

# Firework chemistry: sulfur yellow, barium green, sulfur blue, potassium
# violet, strontium magenta.
FIREWORKS = [[255, 200, 0], [80, 255, 0], [0, 80, 255], [160, 0, 255], [255, 0, 140]]

# Per-phase lighting parameters; [looks.<phase>] in show.toml overrides keys
# (and [looks.<phase>.caldera] overrides keys of the caldera table).
# [looks.transition] sets the phase-change hit's timing.
DEFAULT_LOOKS = {
    # Asleep but smoldering: red breathing, a minority drifts orange/purple.
    # The crater barely glows sulfur blue.
    "dormant": {
        "color": [255, 0, 0],
        "dimmer": [6, 255],
        "pulse_hz": 0.13,
        "accents": [[255, 70, 0], [150, 0, 255]],
        "accent_share": 0.3,
        "accent_hz": 0.04,
        "fog": 0,
        "caldera": {
            "color": [0, 60, 255],
            "dimmer": [0, 110],
            "pulse_hz": 0.08,
            "accents": [[150, 0, 255], [80, 255, 0]],
            "accent_share": 0.34,
            "accent_hz": 0.05,
        },
    },
    # Stirring: orange, faster and livelier; minority fades into red or green.
    # The smoke cycles through firework colors.
    "active": {
        "color": [255, 80, 0],
        "dimmer": [20, 255],
        "pulse_hz": 0.25,
        "accents": [[255, 0, 0], [0, 255, 0]],
        "accent_share": 0.4,
        "accent_hz": 0.07,
        "fog": 180,
        "caldera": {"dimmer": [40, 255], "palette": FIREWORKS, "hold_s": 3.0, "fade": 0.4},
    },
    # The attention signal: an accelerating heartbeat that cascades across
    # every light, each beat a random color from the palette at a random peak.
    "imminent": {
        "dimmer": [0, 255],
        "beat_hz": [0.6, 2.5],
        "palette": [[255, 255, 255], [255, 90, 0], [255, 0, 0]],
        "hit_color": [255, 255, 255],
        "fog": 255,
        "caldera": {"palette": FIREWORKS},
    },
    # Independent red/orange strobes, each flash at its own brightness, kept
    # darker than the crater so the eye goes up. The crater is all white to
    # light the CO2: white pops, then solid full white through every CO2 cue.
    "eruption": {
        "dimmer": [0, 255],
        "strobe_hz": 8.0,
        "palette": [[255, 60, 0], [255, 0, 0]],
        "flash": [0.25, 1.0],
        "hit_color": [255, 255, 255],
        "fog": 255,
        "caldera": {"palette": [[255, 255, 255]], "pop_hz": 3.0, "blast_tail": 0.4},
    },
    "transition": {"gap": 0.15, "step": 0.07, "decay": 0.9},
}

SPREAD = 0.3  # +/- fraction each light's own rate strays from the look's rate
CALDERA_SALT = 1000  # keeps caldera hashes independent of the pars'


def build_looks(raw: dict) -> dict:
    looks = {
        phase: {k: dict(v) if isinstance(v, dict) else v for k, v in params.items()}
        for phase, params in DEFAULT_LOOKS.items()
    }
    for phase, overrides in raw.get("looks", {}).items():
        look = looks.setdefault(phase, {})
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(look.get(key), dict):
                look[key].update(value)
            else:
                look[key] = value
    return looks


def render(
    phase: Phase,
    rig: Rig,
    universe: Universe,
    now: float,
    since: float,
    looks: dict,
    blasts: list[tuple[float, float]] = (),
) -> None:
    """Write every light's ambient look into a blacked-out universe. `since`
    is the time in the current phase; `blasts` are the eruption's CO2 cue
    windows [(start, end)] in eruption time. Pyro is never written here —
    only cues (via overlay + gate) touch flame/CO2."""
    universe.blackout()
    look = looks[phase.value]
    timing = looks["transition"]
    lights = [f for f in rig.fixtures.values() if not f.pyro and "red" in f.channels]
    pars = sorted((f for f in lights if f.name.startswith("par")), key=lambda f: f.name)
    caldera = [f for f in lights if f not in pars]

    lo, hi = look["dimmer"]
    for i, fixture in enumerate(pars):
        level, rgb = _par_look(phase, look, i, len(pars), now, since)
        dimmer, rgb = _transition_hit(look, timing, i, since, lo + (hi - lo) * level, rgb)
        _write(universe, fixture, dimmer, rgb)

    cal = {**look, **look["caldera"]}
    lo, hi = cal["dimmer"]
    for i, fixture in enumerate(caldera):
        level, rgb = _caldera_look(phase, cal, CALDERA_SALT + i, len(caldera), now, since)
        dimmer = lo + (hi - lo) * level
        if phase is Phase.ERUPTION:
            blast = _blast(cal, since, blasts)
            dimmer, rgb = dimmer + (255 - dimmer) * blast, _mix(rgb, [255, 255, 255], blast)
        # The crater hits last, after the sweep reaches the far par.
        dimmer, rgb = _transition_hit(cal, timing, len(pars), since, dimmer, rgb)
        _write(universe, fixture, dimmer, rgb)

    fog = _fog(look, since)
    if fog:
        universe[rig["fogger"].channel("fog")] = fog


def _fog(look: dict, since: float) -> int:
    """`fog` level, pulsed `fog_on` seconds on / `fog_off` off from phase
    entry (on first, so every phase change puffs). Without both, constant."""
    level = look.get("fog", 0)
    on, off = look.get("fog_on"), look.get("fog_off")
    if not level or not on or not off:
        return level
    return level if since % (on + off) < on else 0


def _par_look(phase: Phase, look: dict, i: int, n: int, now: float, since: float):
    """(level 0-1, rgb) for outer par i of n."""
    if phase is Phase.IMMINENT:
        return _heartbeat(look, i, since)
    if phase is Phase.ERUPTION:
        return _strobe(look, i, now)
    return _breathe(look, i, n, now)


def _caldera_look(phase: Phase, look: dict, i: int, n: int, now: float, since: float):
    if phase is Phase.DORMANT:
        return _breathe(look, i, n, now)
    if phase is Phase.ACTIVE:
        return _color_cycle(look, i, now)
    if phase is Phase.IMMINENT:
        return _heartbeat(look, i, since)
    return _pops(look, i, now)


def _breathe(look: dict, i: int, n: int, now: float):
    hz = look["pulse_hz"] * _jitter(i, 1)
    wave = 0.5 + 0.5 * math.sin(2 * math.pi * (hz * now + _rand(i, 2)))
    level = wave * wave  # linger in the dark, bloom at the top

    # Accent episodes: every light swells at the same slow rate, staggered
    # evenly in a shuffled order, and fades toward an accent while its swell
    # is in the top `accent_share` of the cycle. The stagger keeps the
    # accented lights a minority at every moment, not just on average. Each
    # episode picks its accent at the swell's low point, never mid-fade.
    first = i - i % CALDERA_SALT  # this light's group: index range [first, first + n)
    rank = sum(_rand(j, 3) < _rand(i, 3) for j in range(first, first + n))
    p = look["accent_hz"] * now + rank / n
    swell = 0.5 + 0.5 * math.sin(2 * math.pi * p)
    threshold = 0.5 + 0.5 * math.cos(math.pi * look["accent_share"])
    weight = min(1.0, max(0.0, (swell - threshold) / 0.15))
    accents = look["accents"]
    accent = accents[int(_rand(i, math.floor(p + 0.25), 6) * len(accents))]
    return level, _mix(look["color"], accent, weight)


def _color_cycle(look: dict, i: int, now: float):
    """Hold a palette color for about `hold_s`, then crossfade to the next
    over the last `fade` of the hold. Each light walks the palette in its own
    order and at its own pace, so the smoke is always multicolored."""
    palette = look["palette"]
    n = len(palette)
    u = now / (look["hold_s"] * _jitter(i, 20)) + _rand(i, 21)
    k = math.floor(u)
    stride = 1 + int(_rand(i, 22) * (n - 1))  # never repeats a color back to back
    start = int(_rand(i, 23) * n)
    here, there = palette[(start + k * stride) % n], palette[(start + (k + 1) * stride) % n]
    fade = look["fade"]
    w = min(1.0, max(0.0, (u - k - (1 - fade)) / fade))
    wave = 0.5 + 0.5 * math.sin(2 * math.pi * (0.2 * _jitter(i, 24) * now + _rand(i, 25)))
    return 0.6 + 0.4 * wave, _mix(here, there, w * w * (3 - 2 * w))


def _heartbeat(look: dict, i: int, since: float):
    f0, f1 = look["beat_hz"]
    duration = look.get("duration", 30.0)  # injected from imminent_duration
    t = min(since, duration)
    beats = f0 * t + (f1 - f0) * t * t / (2 * duration)  # linear chirp f0 -> f1
    k = math.floor(beats)
    # Each light fires once per beat, at its own random delay into the beat:
    # the beat ripples across the volcano instead of landing all at once.
    x = (beats - k - 0.45 * _rand(i, k, 7)) / 0.5
    if not 0 <= x < 1:
        return 0.0, look["palette"][0]
    pulse = x / 0.12 if x < 0.12 else ((1 - x) / 0.88) ** 3  # snap on, quick fall
    peak = 0.55 + 0.45 * _rand(i, k, 8)
    palette = look["palette"]
    return peak * pulse, palette[int(_rand(i, k, 9) * len(palette))]


def _strobe(look: dict, i: int, now: float):
    cycles = look["strobe_hz"] * _jitter(i, 10) * now + _rand(i, 11)
    k = math.floor(cycles)
    palette = look["palette"]
    rgb = palette[int(_rand(i, k, 12) * len(palette))]
    if cycles - k >= 0.5:
        return 0.0, rgb
    lo, hi = look["flash"]
    return lo + (hi - lo) * _rand(i, k, 13), rgb


def _pops(look: dict, i: int, now: float):
    """Pops: each one snaps on in a random palette color and burns down
    before the next."""
    cycles = look["pop_hz"] * _jitter(i, 30) * now + _rand(i, 31)
    k = math.floor(cycles)
    palette = look["palette"]
    x = cycles - k
    level = x / 0.05 if x < 0.05 else (1 - x) ** 2
    return level, palette[int(_rand(i, k, 32) * len(palette))]


def _blast(look: dict, since: float, blasts) -> float:
    """1 during a CO2 cue window, then a `blast_tail` falloff; 0 elsewhere."""
    tail = look["blast_tail"]
    best = 0.0
    for start, end in blasts:
        if start <= since < end:
            return 1.0
        if end <= since < end + tail:
            best = max(best, 1 - (since - end) / tail)
    return best


def _transition_hit(look: dict, timing: dict, i: int, since: float, dimmer: float, rgb):
    """Full black for `gap`, then light i flashes to full `step * i` later in
    the phase's hit color and decays into the look over `decay`."""
    fire = timing["gap"] + timing["step"] * i
    if since < fire:
        return 0.0, rgb
    x = (since - fire) / timing["decay"]
    if x >= 1:
        return dimmer, rgb
    hit = (1 - x) ** 2
    hit_rgb = look.get("hit_color") or look.get("color") or rgb
    return dimmer + (255 - dimmer) * hit, _mix(rgb, hit_rgb, hit)


def _write(universe: Universe, fixture, dimmer: float, rgb) -> None:
    dimmer = min(255.0, max(0.0, dimmer))
    if "dimmer" in fixture.channels:
        universe[fixture.channel("dimmer")] = round(dimmer)
    else:  # e.g. the fogger's LEDs: no master dimmer, so scale the color
        rgb = [c * dimmer / 255 for c in rgb]
    for name, value in zip(("red", "green", "blue"), rgb):
        universe[fixture.channel(name)] = round(value)


def _mix(a, b, w: float) -> list[float]:
    return [x + (y - x) * w for x, y in zip(a, b)]


def _jitter(i: int, salt: int) -> float:
    return 1 + SPREAD * (2 * _rand(i, salt) - 1)


def _rand(*keys: int) -> float:
    """Deterministic uniform [0, 1) from integer keys (splitmix64 mixing)."""
    mask = (1 << 64) - 1
    x = 0x9E3779B97F4A7C15
    for key in keys:
        x = ((x ^ (key & mask)) * 0xBF58476D1CE4E5B9) & mask
        x ^= x >> 31
        x = (x * 0x94D049BB133111EB) & mask
        x ^= x >> 29
    return (x >> 11) / (1 << 53)
