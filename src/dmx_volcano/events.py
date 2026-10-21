"""Append-only JSONL event log, one file per night.

Everything DESIGN.md says we record flows through here: phase transitions,
eruptions, operator actions, health. Loads straight into pandas:
    pd.read_json("logs/2026-10-31.jsonl", lines=True)
"""

import json
import time
from datetime import datetime, timezone
from pathlib import Path


class EventLog:
    def __init__(self, log_dir: Path) -> None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        self._path = log_dir / f"{stamp}.jsonl"

    def write(self, kind: str, **data) -> None:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "monotonic": time.monotonic(),
            "kind": kind,
            **data,
        }
        try:
            with open(self._path, "a") as f:
                f.write(json.dumps(record) + "\n")
        except OSError as error:
            # The log is for post-season analysis; a full or failing SD card
            # must never take down the frame loop that is writing it.
            print(f"WARNING: event log write failed ({kind}): {error}", flush=True)
