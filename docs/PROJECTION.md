# Projection mapping (parked, 2026-10-27)

Planned add-on: one projector maps lava and other effects onto the volcano's
faces. Parked until a projector is bought; nothing here is implemented yet.

## Projector

One projector, aimed at the side guests see most. Picks from local
Marketplace listings:

| Model | Price | Notes |
|---|---|---|
| **Epson EX3280** (pick) | $200 | 3LCD, 3,600 lm color = white (best for lava reds/oranges), 1024×768 native, 2021, throw 1.44–1.95, **no optical zoom** (distance sets image size) |
| Epson EX5260 | $300 | Same image as the EX3280, 2017, 1.2x optical zoom. Seller has recent no-show reviews |
| ViewSonic PJD6352 | $100 | DLP, 3,500 ANSI (dimmer on saturated colors), 1024×768 native despite "Full HD" in the listing |

At pickup: check lamp hours, dead pixels/dust on a white screen, fan noise,
remote included.

## Placement and safety

- ~15ft back for ~10ft of coverage (throw ≈1.5), which keeps it outside the 3m
  flame exclusion zone.
- Not weatherproof: under cover or in a vented box, away from fog outlets
  (fog in the beam blurs the image).
- Keystone and auto-keystone **off**: any projector-side correction shifts
  the image off the mapped faces.
- Check the menu for a "Direct Power On" setting (powers on when AC is
  applied); turn off with the remote so the lamp fan can cool down.

## Content workflow (laptop bakes, Pi plays)

The Pi never runs mapping software; it plays pre-warped files.

1. Fix the projector in its final position and tape around its feet; a bump
   ruins the alignment.
2. Laptop on HDMI, that display set to 1024×768.
3. Map in **MapMap** (free, open source, quad/mesh warping, no watermark):
   one quad per visible face.
4. Record the projector display with OBS at 1024×768, 30fps. Screen
   recording works with any mapper, export button or not.
5. Export H.264 MP4, one clip per phase (dormant glow, rumble, eruption),
   all recorded from the same mapping.

## Pi playback (to implement)

- Use the existing Pi 4. One 1024×768 stream on one micro-HDMI port is a
  light, hardware-decoded load.
- Run the player (e.g. `mpv --fs --loop`, native resolution, no scaling) as
  its **own low-priority process**, not inside the show engine, so a video
  stall or crash can't hold up DMX or the safety gate. A stalled Pi mid-cue
  could leave the H-E01A safety channel held open by the Enttec's last frame.
- The show engine switches clips on phase change, the same way it switches
  audio pools.
- Before a pyro night: a full-night bench run with video playing, with
  `vcgencmd get_throttled` staying `0x0`. If it throttles or stutters, move
  video to a cheap second Pi.
