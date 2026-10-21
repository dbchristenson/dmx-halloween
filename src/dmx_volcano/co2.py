"""CO2 tank usage tracking and change alerts.

Every granted CO2 burst consumes duration * rate_kg_per_s. Usage persists to
a small JSON state file so a restart never "refills" the tank — only an
explicit tank reset (web UI or deleting the state file) does. When remaining
capacity crosses the warn threshold, record_burst returns True exactly once
per tank so the caller can fire the SMS alert without spamming.

rate_kg_per_s must be MEASURED: weigh the tank, run a timed 4s burst, weigh
again. The config default is a placeholder.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CO2Tracker:
    capacity_kg: float
    rate_kg_per_s: float
    state_path: Path
    warn_remaining_fraction: float = 0.15
    used_kg: float = 0.0
    alerted: bool = False

    def __post_init__(self) -> None:
        if not self.state_path.exists():
            return
        try:
            data = json.loads(self.state_path.read_text())
            self.used_kg = float(data.get("used_kg", 0.0))
            self.alerted = bool(data.get("alerted", False))
        except (OSError, ValueError, AttributeError) as error:
            # Never crash-loop the show over a tank meter. Keep the bad file
            # for inspection and start this tank's count from zero — loudly,
            # because the meter now over-reports what is left.
            aside = self.state_path.with_suffix(".corrupt")
            os.replace(self.state_path, aside)
            print(
                f"WARNING: unreadable CO2 state ({error}); moved to {aside}. "
                "Tank usage restarts from 0 — weigh the tank.",
                flush=True,
            )

    @property
    def remaining_kg(self) -> float:
        return max(0.0, self.capacity_kg - self.used_kg)

    @property
    def remaining_fraction(self) -> float:
        return self.remaining_kg / self.capacity_kg if self.capacity_kg > 0 else 0.0

    def record_burst(self, duration_s: float) -> bool:
        """Log consumption; returns True exactly once per tank when the
        remaining fraction first crosses the warn threshold."""
        self.used_kg += duration_s * self.rate_kg_per_s
        crossed = (
            not self.alerted
            and self.remaining_fraction <= self.warn_remaining_fraction
        )
        if crossed:
            self.alerted = True
        self._save()
        return crossed

    def reset_tank(self) -> None:
        """Call when a fresh tank is connected."""
        self.used_kg = 0.0
        self.alerted = False
        self._save()

    def _save(self) -> None:
        # Write-then-rename: a power cut mid-write leaves the old file whole
        # instead of truncated JSON. ext4's auto_da_alloc flushes the data
        # ahead of a rename-over, so no fsync stall in the frame loop.
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.state_path.with_suffix(".tmp")
        tmp.write_text(json.dumps({"used_kg": self.used_kg, "alerted": self.alerted}))
        os.replace(tmp, self.state_path)
