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

## Remote Control

Open the hamburger menu and choose **Remote Control** (`/remote-control`).
**Home** keeps the existing show console and stage views.

1. While the show is dormant, select **Enable controls**. This holds the show
   dormant and gives you individual light switches, light colors, and fog level.
2. For flame or CO₂, arm pyro, then press and hold the fixture button using
   mouse, touch, Space, or Enter. Release to stop. The flame retains its 0.5 s
   safety-channel lead. The configuration rehearsal lock still applies.
3. Flame stops after at most 10 s of fire, and never fires more than 10 s in
   any 13 s window. There is no fixed cooldown: releasing early books only the
   fire that actually happened, and the next hold gets whatever budget is left.
   CO₂ stops after at most 4 s; cancelling it early keeps the original reserved
   5 s cooldown. A new press is always required. The page shows when each
   fixture is ready (flame: at least 1 s of fire available).
4. **Disable controls** clears overrides; **Disarm pyro** cancels pyro output.
   The show stays held dormant until you release hold on Home.

Holds also end on navigation, focus loss, or a missing heartbeat (0.75 s,
plus the next frame). Server-issued hold tokens cannot be revived after
release or expiry. CO₂ usage is recorded from elapsed hold time when the hold
ends. Existing timed test bursts continue to use their requested duration.
