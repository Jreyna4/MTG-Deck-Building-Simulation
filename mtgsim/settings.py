"""Where the program keeps its files, and a few saved settings.

Everything lives in one folder: ``~/.mtgsim`` in your home folder, or the folder named by the
``MTGSIM_HOME`` environment variable.
"""
from __future__ import annotations

import json
import os
from pathlib import Path


def home() -> Path:
    return Path(os.environ.get("MTGSIM_HOME") or (Path.home() / ".mtgsim"))


def _path() -> Path:
    return home() / "settings.json"


def load() -> dict:
    try:
        return json.loads(_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def get(key: str, default=None):
    return load().get(key, default)


def put(key: str, value) -> None:
    data = load()
    if value in (None, ""):
        data.pop(key, None)
    else:
        data[key] = value
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=1), encoding="utf-8")


# ---- the last deck you loaded, so the window can reopen it ---------------------------------
def save_last_deck(name: str, text: str) -> None:
    path = home() / "last_deck.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"name": name, "text": text}), encoding="utf-8")


def last_deck() -> dict | None:
    try:
        data = json.loads((home() / "last_deck.json").read_text(encoding="utf-8"))
        return data if data.get("text") else None
    except (OSError, ValueError):
        return None
