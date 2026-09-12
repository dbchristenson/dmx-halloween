# dmx-halloween

Stochastic DMX show controller for a Halloween volcano — flame, CO2, fog, and
LED pars driven from a Raspberry Pi. See `DESIGN.md` for architecture and
safety invariants, `HARDWARE.md` for the procurement list.

## Quickstart

```sh
uv run pytest            # test suite (safety invariants live here)
uv run dmx-volcano       # run the show in sim mode (fake driver)
uv run dmx-volcano --real /dev/ttyUSB0   # real Enttec DMX USB Pro
```

Show tuning (dwell distributions, fixture addresses, channel maps) lives in
`config/show.toml` — edit and restart, no code changes. Nightly JSONL event
logs land in `logs/`.

The web console at `http://localhost:8000` has a **Pro view** switch at the
bottom. It shows a Blender-built 3D volcano illuminated by the live DMX
output. Drag or swipe to orbit, scroll or pinch to zoom, and use **Reset
view** to return home. The choice is saved in your browser. All renderer
assets are bundled locally for offline show networks.

See [Pro view](docs/PRO_VIEW.md) for preview assumptions, asset sources, and
browser verification commands.
