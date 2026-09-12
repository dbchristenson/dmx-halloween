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
5. **Secrets** (only if SMS alerts wanted):
   ```sh
   cp deploy/env.example .env && chmod 600 .env   # then fill in Twilio creds
   ```
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

## Audio out (the speaker)

The speaker needs no DMX and never touches the universe — it is a plain
audio device fed line-level from the Pi over a 3.5mm aux cable. Any powered
speaker with an aux/line input works as-is.

**Wiring**: Pi 3.5mm jack → aux cable → speaker line-in. Set the Pi's ALSA
volume high (~85%, `alsamixer`, then `sudo alsactl store` to persist) and do
the real level setting on the speaker's own gain — a hot-ish line signal
into a low amp gain keeps the noise floor down.

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

The audio corpus (`assets/sounds/`) is gitignored — 44MB of WAV doesn't
belong in the repo — so after adding or changing sound files, push them
separately before the restart:

```sh
rsync -av --delete assets/sounds/ dbchristenson@rbpi4-dmx.local:dmx-halloween/assets/sounds/
```

Config-only tuning (show.toml) needs just the restart. Remember the restart
invariant: the service always comes back **disarmed**.
