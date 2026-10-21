"""systemd stop delivers SIGTERM twice (to the cgroup, and again via uv's
forwarding). The second must not abort the blackout-and-shutdown path."""

import json
import signal
import subprocess
import sys
from pathlib import Path

CONFIG = Path(__file__).resolve().parents[1] / "config" / "show.toml"


def test_double_sigterm_still_completes_shutdown(tmp_path):
    # Slow shutdown down so the second signal is guaranteed to land inside it.
    script = tmp_path / "run.py"
    script.write_text(
        "import sys, time\n"
        "from dmx_volcano import show\n"
        "step, shutdown = show.ShowEngine.step, show.ShowEngine.shutdown\n"
        "def first_step(self, now):\n"
        "    show.ShowEngine.step = step\n"
        "    print('running', flush=True)\n"
        "    return step(self, now)\n"
        "def slow_shutdown(self, now):\n"
        "    print('shutting down', flush=True)\n"
        "    time.sleep(0.5)\n"
        "    shutdown(self, now)\n"
        "show.ShowEngine.step = first_step\n"
        "show.ShowEngine.shutdown = slow_shutdown\n"
        f"sys.argv = ['dmx-volcano', '--no-web', '--config', {str(CONFIG)!r}]\n"
        "show.main()\n"
    )
    proc = subprocess.Popen(
        [sys.executable, str(script)], cwd=tmp_path,
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
    )
    try:
        assert proc.stdout.readline().strip() == "running"  # handlers installed
        proc.send_signal(signal.SIGTERM)
        assert proc.stdout.readline().strip() == "shutting down"
        proc.send_signal(signal.SIGTERM)  # the duplicate, mid-shutdown
        _, err = proc.communicate(timeout=10)
    finally:
        proc.kill()

    assert proc.returncode == 0, err
    log = next((tmp_path / "logs").iterdir()).read_text().splitlines()
    assert json.loads(log[-1])["kind"] == "shutdown"
