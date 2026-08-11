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
| Secrets | `/home/pi/dmx-halloween/.env` (mode 600) | Twilio creds via systemd EnvironmentFile, never in git |

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
   Imager's settings gear, set hostname `volcano`, enable SSH, add the
   Starlink WiFi credentials, user `pi`.
2. **Base setup**:
   ```sh
   ssh pi@volcano.local
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
   uv run dmx-volcano      # sim smoke test; browse to http://volcano.local:8000
   ```
4. **Serial + permissions**:
   ```sh
   sudo cp deploy/99-enttec.rules /etc/udev/rules.d/
   sudo udevadm control --reload
   sudo usermod -aG dialout,audio pi
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
time. Control page: `http://volcano.local:8000` from any phone on the
Starlink network.

## Update workflow

Develop and test on the Mac (sim + Docker), then:

```sh
ssh pi@volcano.local "cd dmx-halloween && git pull && uv sync --frozen && sudo systemctl restart dmx-volcano"
```

Config-only tuning (show.toml) needs just the restart. Remember the restart
invariant: the service always comes back **disarmed**.
