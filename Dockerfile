# Emulates the Pi runtime for local testing on the Mac.
#
# Raspberry Pi OS Lite (64-bit, Bookworm) is Debian 12 under the hood and
# Pi 4/5 are arm64 — this image matches the OS userland, CPU architecture,
# Python version (uv-pinned via .python-version), and locked dependencies.
# What it can't emulate: the Enttec on /dev/ttyUSB0 (run sim mode), real
# audio output, and systemd (the container runs the show directly).

FROM debian:bookworm-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates alsa-utils \
    && rm -rf /var/lib/apt/lists/*

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

WORKDIR /app
ENV UV_LINK_MODE=copy UV_PYTHON_INSTALL_DIR=/opt/python

# Dependency layer — cached until the lockfile changes.
COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --frozen --no-install-project

# Project layer.
COPY . .
RUN uv sync --frozen

EXPOSE 8000
CMD ["uv", "run", "dmx-volcano"]
