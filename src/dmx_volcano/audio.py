"""Sound selection, layering, and playback over wired aux out.

Playback shells out to the platform player (afplay on macOS dev, aplay/mpg123
on the Pi) so we carry no audio dependency yet. Layers are just concurrent
player processes; on the Pi that requires the dmix ALSA config in
deploy/asound.conf (bare ALSA otherwise gives the second player "device
busy"). Relative loudness between layers is baked into the files when the
corpus is authored — there is no per-layer gain control here.

Selection lives in SoundBank. Every phase owns role pools
(config/show.toml [audio.phases.<phase>]):

  start  — one-shot played on phase entry
  loop   — the base bed: picked at random, respawned each time it ends
           (a multi-file pool re-picks per cycle, so the bed evolves)
  bg     — a second looping texture layer over the loop
  accent — one-shots sprinkled at random intervals (accent_interval) while
           the phase runs

plus transition stingers ([audio.transitions] "dormant->active") layered on
top when the show makes exactly that move. Every pick is random and never
repeats the previous pick while the pool has alternatives. Missing files are
tolerated at play time (sim mode without assets) and reported by
missing_files() so startup can flag them instead of opening night.

LayeredPlayer owns the running processes for the current phase and is
ticked from the frame loop.
"""

import random
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

from .phases import Phase

_PLAYERS = ["afplay", "aplay", "mpg123"]

# Files the importer accepts and the asset listing shows.
AUDIO_SUFFIXES = {".wav", ".mp3", ".ogg", ".flac", ".aiff", ".aif", ".m4a"}

ROLES = ("start", "loop", "bg", "accent")

# Accents already sounding before another may fire — caps process spam when
# the sim clock fast-forwards and keeps real nights from stacking chaos.
MAX_CONCURRENT_ACCENTS = 3


def play(path: Path) -> subprocess.Popen | None:
    """Fire-and-forget playback; returns the process handle, or None if no
    player or file is missing (sim mode without assets is fine)."""
    if not path.exists():
        return None
    for player in _PLAYERS:
        if shutil.which(player):
            return subprocess.Popen([player, str(path)])
    return None


def stop(proc: subprocess.Popen | None) -> None:
    """Stop a previously started player, tolerating already-dead processes."""
    if proc is None or proc.poll() is not None:
        return
    proc.terminate()
    try:
        proc.wait(timeout=1)
    except subprocess.TimeoutExpired:
        proc.kill()


def safe_asset_name(name: str) -> str:
    """Sanitize an imported filename: basename only, known audio suffix."""
    clean = Path(name.strip()).name
    if not clean or clean.startswith("."):
        raise ValueError("empty or hidden filename")
    if Path(clean).suffix.lower() not in AUDIO_SUFFIXES:
        allowed = ", ".join(sorted(AUDIO_SUFFIXES))
        raise ValueError(f"unsupported audio type {Path(clean).suffix!r} — use {allowed}")
    return clean


def resolve_asset(assets_dir: Path, name: str) -> Path:
    """Resolve a config-style relative path (subfolders allowed, e.g.
    "sounds/active/loop_1.wav") to a file strictly inside assets/."""
    rel = Path(name.strip())
    parts = rel.parts
    if rel.is_absolute() or not parts or any(p.startswith(".") for p in parts):
        raise ValueError(f"invalid asset path {name!r}")
    if rel.suffix.lower() not in AUDIO_SUFFIXES:
        allowed = ", ".join(sorted(AUDIO_SUFFIXES))
        raise ValueError(f"unsupported audio type {rel.suffix!r} — use {allowed}")
    return assets_dir / rel


def list_assets(assets_dir: Path) -> list[str]:
    """Audio files present anywhere under assets/, as sorted relative paths."""
    if not assets_dir.is_dir():
        return []
    return sorted(
        p.relative_to(assets_dir).as_posix()
        for p in assets_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in AUDIO_SUFFIXES
    )


def _pool(value: str | list[str]) -> list[str]:
    """Config accepts a single filename or a list of them."""
    return [value] if isinstance(value, str) else list(value)


_PHASE_NAMES = {p.value for p in Phase}


@dataclass
class PhaseAudio:
    """The role pools for one phase."""

    start: list[str] = field(default_factory=list)
    loop: list[str] = field(default_factory=list)
    bg: list[str] = field(default_factory=list)
    accent: list[str] = field(default_factory=list)
    accent_interval: tuple[float, float] = (10.0, 30.0)

    @classmethod
    def from_config(cls, phase: str, value) -> "PhaseAudio":
        # Legacy shorthand — a bare file/list is a one-shot on entry.
        if isinstance(value, (str, list)):
            return cls(start=_pool(value))
        pools = cls()
        for key, sub in value.items():
            if key == "accent_interval":
                lo, hi = float(sub[0]), float(sub[1])
                if not 0 < lo <= hi:
                    raise ValueError(
                        f"[audio.phases.{phase}] accent_interval must be "
                        f"[min, max] with 0 < min <= max, got {sub!r}"
                    )
                pools.accent_interval = (lo, hi)
            elif key in ROLES:
                setattr(pools, key, _pool(sub))
            else:
                raise ValueError(
                    f"[audio.phases.{phase}] unknown role {key!r} "
                    f"(known: {', '.join(ROLES)}, accent_interval)"
                )
        return pools

    def files(self) -> set[str]:
        return {f for role in ROLES for f in getattr(self, role)}


@dataclass
class SoundBank:
    """Role pools per phase plus stinger pools per transition (frm, to)."""

    assets_dir: Path
    phases: dict[str, PhaseAudio] = field(default_factory=dict)
    transitions: dict[tuple[str, str], list[str]] = field(default_factory=dict)
    rng: random.Random = field(default_factory=random.Random)
    _last: dict[str, str] = field(default_factory=dict, repr=False)

    @classmethod
    def from_config(cls, raw: dict, assets_dir: Path) -> "SoundBank":
        """Parse the [audio] table.

        [audio.phases.<phase>] holds role pools (start/loop/bg/accent +
        accent_interval); a bare file or list is the legacy one-shot-on-entry
        shorthand, as is the flat form [audio] active = "rumble.wav".
        [audio.transitions] "dormant->active" holds stinger pools. Unknown
        phase or role names are a config error — fail loud at startup, not
        silently at show time.
        """
        phases: dict[str, PhaseAudio] = {}
        transitions: dict[tuple[str, str], list[str]] = {}
        for key, value in raw.items():
            if key == "phases":
                for phase, sub in value.items():
                    phases[phase] = PhaseAudio.from_config(phase, sub)
            elif key == "transitions":
                for arrow, pool in value.items():
                    frm, sep, to = (part.strip() for part in arrow.partition("->"))
                    if not sep or not frm or not to:
                        raise ValueError(
                            f"[audio.transitions] key must look like "
                            f"'dormant->active', got {arrow!r}"
                        )
                    transitions[(frm, to)] = _pool(pool)
            else:  # legacy flat form
                phases[key] = PhaseAudio.from_config(key, value)

        for name in list(phases) + [n for pair in transitions for n in pair]:
            if name not in _PHASE_NAMES:
                raise ValueError(
                    f"[audio] references unknown phase {name!r} "
                    f"(known: {', '.join(sorted(_PHASE_NAMES))})"
                )
        return cls(assets_dir=assets_dir, phases=phases, transitions=transitions)

    def phase(self, phase: str) -> PhaseAudio | None:
        return self.phases.get(phase)

    def phase_sound(self, phase: str, role: str) -> Path | None:
        pools = self.phases.get(phase)
        pool = getattr(pools, role) if pools else None
        return self._pick(f"{phase}:{role}", pool)

    def transition_sound(self, frm: str, to: str) -> Path | None:
        return self._pick(f"{frm}->{to}", self.transitions.get((frm, to)))

    def accent_delay(self, phase: str) -> float:
        lo, hi = self.phases[phase].accent_interval
        return self.rng.uniform(lo, hi)

    def _pick(self, key: str, pool: list[str] | None) -> Path | None:
        if not pool:
            return None
        fresh = [f for f in pool if f != self._last.get(key)] or pool
        choice = self.rng.choice(fresh)
        self._last[key] = choice
        return self.assets_dir / choice

    def referenced(self) -> set[str]:
        """Every filename any pool can play."""
        files = {f for pools in self.phases.values() for f in pools.files()}
        files |= {f for pool in self.transitions.values() for f in pool}
        return files

    def missing_files(self) -> list[str]:
        """Referenced files not present in assets/ — flag these at startup."""
        return sorted(f for f in self.referenced() if not (self.assets_dir / f).exists())

    def describe(self) -> dict:
        """JSON-friendly view of the pools for the control page."""
        missing = set(self.missing_files())

        def entries(pool: list[str]) -> list[dict]:
            return [{"file": f, "missing": f in missing} for f in pool]

        return {
            "phases": {
                phase: {
                    "roles": {
                        role: entries(getattr(pools, role))
                        for role in ROLES
                        if getattr(pools, role)
                    },
                    "accent_interval": list(pools.accent_interval),
                }
                for phase, pools in self.phases.items()
            },
            "transitions": {
                f"{frm}->{to}": entries(pool)
                for (frm, to), pool in self.transitions.items()
            },
        }


class LayeredPlayer:
    """The running audio for the current phase, ticked from the frame loop.

    On phase entry: the old phase's layers stop, the transition stinger (if
    that exact move has one) and the new phase's `start` one-shot fire, and
    the `loop`/`bg` layers spawn. Each tick: ended loop/bg players respawn
    with a fresh pick, and `accent` one-shots fire at randomly sampled
    intervals. Ticks use show time, so accents freeze with the sim clock's
    pause and compress under fast-forward (capped by
    MAX_CONCURRENT_ACCENTS).
    """

    def __init__(self, bank: SoundBank) -> None:
        self.bank = bank
        self.phase: str | None = None  # None until the first enter_phase
        self._stinger: subprocess.Popen | None = None
        self._start: subprocess.Popen | None = None
        self._layers: dict[str, subprocess.Popen | None] = {"loop": None, "bg": None}
        self._accents: list[subprocess.Popen] = []
        self._accent_at: float | None = None

    def enter_phase(self, frm: str, to: str) -> None:
        self.stop_all()
        self.phase = to
        self._accent_at = None  # first accent scheduled on the next tick
        stinger = self.bank.transition_sound(frm, to)
        self._stinger = play(stinger) if stinger else None
        one_shot = self.bank.phase_sound(to, "start")
        self._start = play(one_shot) if one_shot else None
        for role in self._layers:
            self._layers[role] = self._spawn(role)

    def tick(self, now: float) -> None:
        if self.phase is None:
            return
        # Respawn a looping layer when its player exits — never retried when
        # the spawn itself failed (missing file / no player): that layer just
        # stays silent for the phase.
        for role, proc in self._layers.items():
            if proc is not None and proc.poll() is not None:
                self._layers[role] = self._spawn(role)

        pools = self.bank.phase(self.phase)
        if pools is None or not pools.accent:
            return
        if self._accent_at is None:
            self._accent_at = now + self.bank.accent_delay(self.phase)
            return
        if now >= self._accent_at:
            self._accents = [p for p in self._accents if p.poll() is None]
            if len(self._accents) < MAX_CONCURRENT_ACCENTS:
                path = self.bank.phase_sound(self.phase, "accent")
                proc = play(path) if path else None
                if proc is not None:
                    self._accents.append(proc)
            self._accent_at = now + self.bank.accent_delay(self.phase)

    def stop_all(self) -> None:
        stop(self._stinger)
        stop(self._start)
        for proc in self._layers.values():
            stop(proc)
        for proc in self._accents:
            stop(proc)
        self._stinger = self._start = None
        self._layers = {"loop": None, "bg": None}
        self._accents = []
        self.phase = None
        self._accent_at = None

    def _spawn(self, role: str) -> subprocess.Popen | None:
        path = self.bank.phase_sound(self.phase, role)
        return play(path) if path else None
