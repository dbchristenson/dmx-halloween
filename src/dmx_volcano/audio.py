"""Audio playback over wired aux out.

Stub: shells out to the platform player (afplay on macOS dev, aplay/mpg123 on
the Pi) so we carry no audio dependency yet. Revisit if we need gapless loops
for the active-phase rumble — likely candidates: simpleaudio or miniaudio.
"""

import shutil
import subprocess
from pathlib import Path

_PLAYERS = ["afplay", "aplay", "mpg123"]


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
