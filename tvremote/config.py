"""Persistent settings (pairing key, favourites, fast-UI switch) in data/state.json."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
STATE_FILE = Path(os.environ.get("TVREMOTE_STATE", ROOT / "data" / "state.json"))

DEFAULTS: dict[str, Any] = {
    # found on the network on first run (SSDP), or set with --tv
    "tv_host": None,
    # learned from the TV after pairing; used for Wake-on-LAN power-on
    "tv_mac": None,
    "client_key": None,
    # app ids pinned to the fast launcher and the phone's favourites row, in order
    "favorites": [],
    # off = everything behaves exactly like the stock LG UI
    "fast_ui": True,
    # open Quick Launch automatically whenever the TV turns on
    "launch_on_power": True,
    # id of the sideloaded launcher app (see tv/appinfo.json)
    "launcher_app_id": "com.tvupgrade.quicklaunch",
    "port": 8080,
    # LG Developer Mode session token; the server renews the session daily
    # so the Quick Launch app is never removed (see install_tv_app.py)
    "dev_token": None,
}


class Config:
    def __init__(self, path: Path = STATE_FILE) -> None:
        self.path = path
        self.data: dict[str, Any] = dict(DEFAULTS)
        if path.exists():
            self.data.update(json.loads(path.read_text()))

    def __getitem__(self, key: str) -> Any:
        return self.data[key]

    def __setitem__(self, key: str, value: Any) -> None:
        self.data[key] = value
        self.save()

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=2))
        tmp.replace(self.path)
