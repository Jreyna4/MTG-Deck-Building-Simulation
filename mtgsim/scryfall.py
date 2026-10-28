"""Card data from the free Scryfall API (https://scryfall.com/docs/api), with a local cache.

Cards are looked up 75 at a time with the /cards/collection endpoint, then saved to a JSON
cache so later runs work offline and don't hit Scryfall again.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from . import settings
from .cards import Card

API = "https://api.scryfall.com"
HEADERS = {
    "User-Agent": "MTGDeckBuildingSimulation/0.1",
    "Accept": "application/json",
    "Content-Type": "application/json",
}
_KEEP = ("name", "mana_cost", "cmc", "type_line", "oracle_text", "colors", "color_identity",
         "produced_mana", "power", "toughness", "loyalty", "keywords", "layout", "scryfall_uri",
         "edhrec_rank")
_KEEP_FACE = ("name", "mana_cost", "type_line", "oracle_text", "power", "toughness", "colors")


class ScryfallError(RuntimeError):
    pass


def default_cache_path() -> Path:
    return settings.home() / "card_cache.json"


def _trim(card: dict) -> dict:
    out = {k: card[k] for k in _KEEP if k in card}
    if card.get("card_faces"):
        out["card_faces"] = [{k: f[k] for k in _KEEP_FACE if k in f} for f in card["card_faces"]]
        img = (card["card_faces"][0].get("image_uris") or {}).get("normal")
    else:
        img = (card.get("image_uris") or {}).get("normal")
    if img:
        out["image_uris"] = {"normal": img}
    if card.get("prices"):
        out["prices"] = {"usd": card["prices"].get("usd")}
    return out


class CardCache:
    """A JSON file mapping lower-case card names (and face names) to trimmed Scryfall data."""

    def __init__(self, path: str | Path | None = None):
        self.path = Path(path) if path else default_cache_path()
        self.data: dict = {}
        if self.path.exists():
            try:
                self.data = json.loads(self.path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                self.data = {}

    def get(self, name: str) -> dict | None:
        return self.data.get(name.lower())

    def put(self, card: dict, *aliases: str) -> None:
        trimmed = _trim(card)
        keys = {card["name"].lower(), *(a.lower() for a in aliases if a)}
        for face in card.get("card_faces") or []:
            if face.get("name"):
                keys.add(face["name"].lower())
        for k in keys:
            self.data[k] = trimmed

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.data, indent=0), encoding="utf-8")
        tmp.replace(self.path)


def _request(url: str, body: dict | None = None, timeout: float = 30) -> dict:
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=HEADERS, method="POST" if body else "GET")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return {"object": "error", "status": 404}
        raise ScryfallError(f"Scryfall answered HTTP {e.code} for {url}") from e
    except urllib.error.URLError as e:
        raise ScryfallError(f"Couldn't reach Scryfall ({e.reason}). Check your internet connection.") from e


def fetch_cards(names: list, cache: CardCache | None = None, offline: bool = False,
                progress=None) -> tuple[dict, list]:
    """Look up card names. Returns ({requested name: Card}, [names not found]).

    Uses the cache first; anything missing is fetched from Scryfall (unless ``offline``)."""
    cache = cache or CardCache()
    wanted = list(dict.fromkeys(n.strip() for n in names if n.strip()))
    found: dict = {}
    missing = []
    for n in wanted:
        hit = cache.get(n)
        if hit:
            found[n] = Card.from_scryfall(hit)
        else:
            missing.append(n)
    if missing and not offline:
        changed = False
        for i in range(0, len(missing), 75):
            batch = missing[i:i + 75]
            if progress:
                progress(f"Fetching {len(batch)} cards from Scryfall...")
            result = _request(f"{API}/cards/collection", {"identifiers": [{"name": n} for n in batch]})
            by_name = {}
            for card in result.get("data", []):
                by_name[card["name"].lower()] = card
                for face in card.get("card_faces") or []:
                    by_name.setdefault(face.get("name", "").lower(), card)
            for n in batch:
                card = by_name.get(n.lower())
                if card:
                    cache.put(card, n)
                    found[n] = Card.from_scryfall(cache.get(n))
                    changed = True
            time.sleep(0.1)  # Scryfall asks for 50-100 ms between requests
        # Second chance for typos / alternate spellings: the fuzzy named endpoint
        for n in [n for n in missing if n not in found]:
            card = _request(f"{API}/cards/named?" + urllib.parse.urlencode({"fuzzy": n}))
            time.sleep(0.1)
            if card.get("object") == "card":
                cache.put(card, n)
                found[n] = Card.from_scryfall(cache.get(n))
                changed = True
        if changed:
            cache.save()
    not_found = [n for n in wanted if n not in found]
    return found, not_found
