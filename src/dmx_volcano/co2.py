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
        if self.state_path.exists():
            data = json.loads(self.state_path.read_text())
            self.used_kg = data.get("used_kg", 0.0)
            self.alerted = data.get("alerted", False)

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
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        self.state_path.write_text(
            json.dumps({"used_kg": self.used_kg, "alerted": self.alerted})
        )
