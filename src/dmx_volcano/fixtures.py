"""Fixture channel maps, loaded from config/show.toml.

A Fixture maps human-readable channel names ("safety", "fire", "red") to
absolute DMX channel numbers via its start address. Channel maps live in
config, not code — they are provisional until confirmed against each
fixture's printed manual (see DESIGN.md open items).
"""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class Fixture:
    name: str
    address: int  # DMX start address, 1-indexed
    channels: dict[str, int]  # name -> offset from address (0-based)
    pyro: bool = False  # True for flame/CO2: subject to safety clamping
    position: tuple[float, float] = (0.0, 0.0)  # meters, for the visualizer
    tilt: float | None = None  # beam degrees above horizontal, for the visualizer

    def channel(self, name: str) -> int:
        return self.address + self.channels[name]


@dataclass
class Rig:
    fixtures: dict[str, Fixture] = field(default_factory=dict)

    def __getitem__(self, name: str) -> Fixture:
        return self.fixtures[name]

    def pyro_channels(self) -> set[int]:
        """Every absolute DMX channel belonging to a pyro fixture."""
        return {
            fixture.channel(ch)
            for fixture in self.fixtures.values()
            if fixture.pyro
            for ch in fixture.channels
        }


def load_rig(config_path: Path) -> Rig:
    with open(config_path, "rb") as f:
        raw = tomllib.load(f)
    rig = Rig()
    for name, spec in raw.get("fixtures", {}).items():
        rig.fixtures[name] = Fixture(
            name=name,
            address=spec["address"],
            channels=spec["channels"],
            pyro=spec.get("pyro", False),
            position=tuple(spec.get("position", (0.0, 0.0))),
            tilt=spec.get("tilt"),
        )
    check_addresses(rig)
    return rig


def check_addresses(rig: Rig) -> None:
    """Refuse a rig where any channel is out of range or claimed twice.

    An overlap is how a PAR's color channel ends up driving the flame
    machine's fire channel — unclamped, because the gate only knows the pyro
    channels at the flame's *configured* address. Fail at startup, loudly."""
    owner: dict[int, str] = {}
    for fixture in rig.fixtures.values():
        for name in fixture.channels:
            ch = fixture.channel(name)
            if not 1 <= ch <= 512:
                raise ValueError(f"{fixture.name}.{name} is DMX channel {ch}, outside 1-512")
            if ch in owner:
                raise ValueError(
                    f"DMX channel {ch} is claimed by both {owner[ch]} and "
                    f"{fixture.name}.{name} — fix the addresses in config"
                )
            owner[ch] = f"{fixture.name}.{name}"
