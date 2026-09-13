# Pro volcano view

The bottom Pro view switch replaces the existing top-down stage with an
orbitable 3D preview; show controls continue using the existing API. The
choice persists on this browser. The model and renderer load only on demand.

Drag or swipe to orbit, scroll or pinch to zoom. Focus the canvas to use
arrow keys, +/−, and Home, or use the on-screen zoom and Reset view buttons.
Crater close-up frames the actual machine models; Entire effect fits the
volcano and the full CO₂ column, including after a mobile viewport resize.
Preview flame and Preview CO₂ run 2.5-second visual previews, labeled
"Visual preview · no DMX output". These affect only the renderer, preserve
the live frame, expire automatically, and stop when Pro view is switched off.
The renderer stops drawing when the stage is off screen, the tab is hidden,
or Pro view is off. Reduced-motion preferences disable camera inertia and
decorative plume/flame movement; actual output changes remain visible.

The existing light/dark console palette and Avenir typography remain the UI
foundation. A dark slate stage makes surface lighting easy to see. The volcano
is the focus; a compact row of fixture color readings explains the live light
sources. Everything stays inside the existing stage panel.

This is a visual approximation of the planned prop, not a measured digital
twin or a photometric prediction. The 3.05 m height follows `project-intro.md`;
the silhouette and assumed light aim are illustrative. The preview follows
the same 100 ms state polling as the top view, not every 40 Hz DMX packet.
Neutral viewing light keeps the unlit model visible. No preview interaction
sends control requests or changes the show.

## Assets and lighting

- `assets/models/volcano.blend`: editable source, created through Blender MCP.
- `src/dmx_volcano/web/static/models/volcano.glb`: one scene with the rocky
  shell, recessed crater, mineral vertex colors, and loose basalt. About
  746 KiB; no external textures or decoder downloads.
- `tools/build_volcano_asset.py`: original procedural modeling recipe. To
  regenerate through Blender MCP, set `PROJECT_ROOT` to the absolute checkout
  path and execute this script's contents in Blender. It creates a separate
  scene and restores the previous scene after a successful export.
- `assets/models/pyro-fixtures.blend` and `static/models/pyro-fixtures.glb`:
  original Blender MCP models of the flamethrower and CO₂ cannon. Housings
  include nozzle openings, valve enclosures, displays, fittings, and hoses.
  The renderer places them using the configured plan positions; the 3 m
  crater-deck mounting elevation is illustrative. Outlet markers in the GLB
  place each effect at the actual modeled nozzle. Regenerate with
  `tools/build_pyro_assets.py` through Blender MCP with `PROJECT_ROOT` set.
- `static/vendor/three/`: pinned Three.js 0.180.0 and MIT license. The three
  addon imports use local relative paths; the app makes no CDN requests.

Fixture positions come from `/api/layout`: plan `(x, north)` becomes scene
`(x, height, -north)`. Exterior RGB fixtures aim at the upper slope; interior
fixtures sit at crater height. These heights and aims are preview assumptions.
RGB and master dimmers, including fixtures without master dimmers, come from
the post-safety `/api/state` frame. Spotlights illuminate the mesh and cast
shadows. Fog, CO₂, and flame previews follow their respective output channels;
flame also requires its safety channel in the configured hardware's open range.

The flame is a two-meter, three-dimensional turbulent jet with a bright core
and warm light spilling onto the crater. The CO₂ effect is a dense nine-meter
column of soft, rising puffs, expanding from the nozzle over about 0.45 seconds.
Both stop when their output clears; reduced motion freezes turbulence while
preserving a fully visible effect. These are visual approximations, not fluid
simulations. The envelopes follow the manufacturer's published ranges:
[H-E01A: 1–2 m flame, 18.5 × 18.5 × 29.5 cm housing](https://www.mokalighting.com/products/moka-sfx-h-e01a-lgp-single-head-flame-mchine)
and [MK-C09: 8–10 m CO₂ column](https://www.mokalighting.com/products/moka-sfx-mk-c09-special-effects-dj-co2-jet).

If the model fails to load or the graphics context is lost, Retry rebuilds the
view. A failed renderer module download offers Reload page, which clears the
browser's cached failed imports. The top view remains available via the switch.

## Verification

```sh
uv run pytest -q
node --test tests/test_pro_lighting.mjs
node --test tests/test_pro_effects.mjs
uv run dmx-volcano                    # fake DMX driver; local server
uv run --with playwright --with pillow tests/check_pro_browser.py
uv run --with playwright --with pillow tests/check_pyro_browser.py
```

The standalone browser check requires Chrome installed. It uses an isolated
headless profile and software rendering; known frames are injected only into
that browser's network responses. It sends no show-control requests. It checks
mesh pixel changes for DMX colors, mouse/keyboard/touch orbit, pinch zoom,
reset during camera inertia, persistence, mobile layout, local-only requests,
and model/module load recovery. Screenshots are saved in `/tmp/dmx-pro-qa`.
The pyro check verifies both hardware models, nozzle placement, visible flame
and CO₂ pixels, live safety gating, full-column camera fitting on desktop and
mobile, automatic preview expiry, and absence of control POSTs. Its images
are saved in `/tmp/dmx-pyro-qa`.
