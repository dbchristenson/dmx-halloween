# Deployment: Raspberry Pi Environment

## The environment, exactly

| Layer | Choice | Why |
|---|---|---|
| OS | **Raspberry Pi OS Lite (64-bit), Bookworm** | Debian 12 base, headless (no desktop wasting RAM), the reference OS for Pi hardware |
| Architecture | arm64 (Pi 4 or 5) | |
| Python | **3.13, installed and pinned by uv** (`.python-version`) | identical interpreter on Mac, Docker, and Pi — apt's Python is never used |
| Dependencies | `uv sync --frozen` from `uv.lock` | bit-for-bit the locked versions everywhere |
| Process manager | systemd (`deploy/dmx-volcano.service`) | auto-start on boot into safe dormant, auto-restart on crash, journald logging |
| Serial | `/dev/enttec` via udev rule (`deploy/99-enttec.rules`) | stable name regardless of USB enumeration |
| Secrets | `/home/dbchristenson/dmx-halloween/.env` (mode 600) | Twilio creds via systemd EnvironmentFile, never in git |

## Local emulation with Docker (on the Mac)

The `Dockerfile` reproduces this stack: `debian:bookworm-slim` (same userland
as Pi OS Lite), forced `linux/arm64` (same architecture — native speed on
Apple Silicon), uv-installed Python 3.13, `uv sync --frozen`. What it cannot
emulate: the Enttec serial device (use sim mode), audible sound, and systemd.

```sh
# Build the Pi-equivalent image
docker build --platform linux/arm64 -t dmx-volcano .

# Run the test suite in the Pi environment
docker run --rm --platform linux/arm64 dmx-volcano uv run pytest -q

# Run the sim show; visualizer at http://localhost:8000
docker compose up            # or:
docker run --rm --platform linux/arm64 -p 8000:8000 dmx-volcano
```

If tests pass here, the only Pi-specific unknowns left are the physical ones
(serial port, audio device, WiFi) — the code and dependency stack are
already proven on the target OS and architecture.

## Pi bring-up (once, ~30 minutes)

1. **Flash**: Raspberry Pi Imager → "Raspberry Pi OS Lite (64-bit)". In the
   Imager's settings gear, set hostname `RBPI4-DMX`, enable SSH, add WiFi
   credentials (home WiFi for bring-up; at the venue the Pi uses ethernet
   into the Starlink router), user `dbchristenson`.
2. **Base setup**:
   ```sh
   ssh dbchristenson@rbpi4-dmx.local
   sudo apt update && sudo apt full-upgrade -y
   sudo apt install -y git alsa-utils
   curl -LsSf https://astral.sh/uv/install.sh | sh   # installs to ~/.local/bin
   ```
3. **Project**:
   ```sh
   git clone <repo-url> ~/dmx-halloween
   cd ~/dmx-halloween
   uv sync --frozen        # fetches Python 3.13 + locked deps
   uv run pytest -q        # prove the environment on the Pi itself
   uv run dmx-volcano      # sim smoke test; browse to http://rbpi4-dmx.local:8000
   ```
4. **Serial + permissions**:
   ```sh
   sudo cp deploy/99-enttec.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   sudo usermod -aG dialout,audio dbchristenson
   # replug the Enttec, then verify: ls -l /dev/enttec
   ```
5. **Secrets** (the arm PIN is required; Twilio only if SMS alerts wanted):
   ```sh
   cp deploy/env.example .env && chmod 600 .env   # set DMX_ARM_PIN, Twilio creds
   ```
   Without `DMX_ARM_PIN` the control page refuses to arm. Disarm never
   asks for it.
6. **Service**:
   ```sh
   sudo cp deploy/dmx-volcano.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now dmx-volcano
   journalctl -u dmx-volcano -f     # watch it come up
   ```

After this, the Pi boots straight into the dormant show, disarmed, every
time. Control page: `http://rbpi4-dmx.local:8000` from any phone on the
same network as the Pi (Starlink at the venue).

## Audio out (the speakers)

The speakers need no DMX and never touch the universe — they are plain
audio devices fed line-level from the Pi's 3.5mm jack: an **Alto TS408**
(8" powered PA top) and a **Polk PSW10** (10" home subwoofer). See
`HARDWARE.md` § 6 for specs and cables.

**Wiring**: Pi 3.5mm jack → 3.5mm stereo splitter, then:

- 3.5mm → dual ¼" TS cable → TS408 inputs (left → ch1, right → ch2).
  Both channel switches on **Line**.
- 3.5mm → dual RCA cable → PSW10 RCA line in.

(The TS408's XLR link out could feed the sub instead of the splitter, but it
is unconfirmed whether it carries both inputs or just ch1 — check the TS408
manual before relying on it.)

**Power order**: Pi first, speakers last; speakers off first. Plugging or
unplugging the aux cables with the speakers on and up sends a thump through
them.

**Setting levels**: set the Pi's ALSA volume high (~85%, `alsamixer`, then
`sudo alsactl store` to persist) and leave it; all real level setting happens
on the speakers. A hot-ish line signal into a low amp gain keeps the noise
floor down. The Pi's jack is consumer-level, so the TS408 gain may sit
fairly high — that's normal (the corpus also carries 6dB of stacking
headroom, docs/AUDIO.md § Loudness). With the loudest eruption file playing from the
Audio panel:

1. **TS408**: raise the channel gains to show level. The red LED is the
   built-in limiter working — occasional flicker on peaks is fine, steady red
   means back off.
2. **PSW10**: crossover to **80–100Hz** (it handles only what the TS408
   can't), phase whichever sounds fuller from the audience spot. Raise its
   gain just until the bass blends, and stop well short of port chuffing,
   flapping, or knocking. It has a 50W home amp and no confirmed limiter —
   hours of rumble beds are harder on it than the 10s test, so leave margin.
3. Judge loudness from where guests stand: the eruption should feel big, but
   nobody should be blasted at close range for long.

Keep the sub dry and ventilated (its amp is the back panel) — it is a home
sub, not built for outdoor nights.

**Software mixing is required.** The engine layers several sounds at once
(loop bed + bg + accents + transition stingers = one player process each),
and Pi OS Lite's bare ALSA only lets one process open the device — every
other layer dies with "device busy". Install the dmix config:

```sh
sudo cp deploy/asound.conf /etc/asound.conf
# verify: both must sound at the same time
aplay assets/<a>.wav & aplay assets/<b>.wav
```

**Known quirk**: the Pi 4's 3.5mm jack is PWM-driven — through a big
amplified speaker expect an audible hiss/noise floor. If it bothers you at
show volume, a $10–15 class-compliant USB DAC fixes it: plug it in, find its
card number with `aplay -l`, set that number in `/etc/asound.conf` (both
places), done. Nothing in the show code changes; try the jack first.

**Line check without running the show**: on the control page's Audio panel,
click any file chip — it plays out the speaker through the exact production
path (`POST /api/audio/preview`). Do this at the venue after cabling, before
opening. Startup also logs an `audio_missing` event (and a journal warning)
if config references files that aren't in `assets/`.

## Update workflow

Develop and test on the Mac (sim + Docker), then:

```sh
ssh dbchristenson@rbpi4-dmx.local "cd dmx-halloween && git pull && uv sync --frozen && sudo systemctl restart dmx-volcano"
```

The audio corpus (`assets/sounds/`) is gitignored — licensed WAVs don't
belong in a public repo — so after re-rendering it (`uv run
tools/prep_audio.py`, docs/AUDIO.md), push it separately before the restart
(`assets/raw/` stays on the Mac):

```sh
rsync -av --delete assets/sounds/ dbchristenson@rbpi4-dmx.local:dmx-halloween/assets/sounds/
```

Config-only tuning (show.toml) needs just the restart. Remember the restart
invariant: the service always comes back **disarmed**.
