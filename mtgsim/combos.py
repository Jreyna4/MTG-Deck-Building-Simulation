"""Combo finder using Commander Spellbook (https://commanderspellbook.com).

Sends your deck list to Spellbook's free ``find-my-combos`` endpoint and returns:

* combos you already have every card for
* combos you're one or two cards away from (with the missing cards named)
* which missing cards would complete the most combos
"""
from __future__ import annotations

import hashlib
import json
import os
import time
import urllib.error
import urllib.request
from collections import Counter
from pathlib import Path

API = "https://backend.commanderspellbook.com/find-my-combos"
COMBO_URL = "https://commanderspellbook.com/combo/{id}/"
WIN_WORDS = ("win the game", "infinite damage", "infinite combat damage", "infinite lifeloss",
             "infinite life loss", "each opponent loses the game", "infinite poison")
CACHE_DAYS = 7


class ComboError(RuntimeError):
    pass


def _get(d: dict, snake: str, default=None):
    """Spellbook answers in camelCase; accept snake_case too."""
    if snake in d:
        return d[snake]
    parts = snake.split("_")
    camel = parts[0] + "".join(p.title() for p in parts[1:])
    return d.get(camel, default)


def _variant(v: dict, deck_names: set) -> dict:
    cards = [_get(u, "card", {}).get("name", "?") for u in _get(v, "uses", [])]
    templates = [_get(r, "template", {}).get("name", "?") for r in _get(v, "requires", [])]
    results = [_get(p, "feature", {}).get("name", "?") for p in _get(v, "produces", [])]
    low_deck = {n.lower() for n in deck_names} | {n.split(" // ")[0].lower() for n in deck_names}
    missing = [c for c in cards if c.lower() not in low_deck and c.split(" // ")[0].lower() not in low_deck]
    prereq = " ".join(x for x in (_get(v, "easy_prerequisites", ""), _get(v, "notable_prerequisites", "")) if x)
    return {
        "id": v.get("id"),
        "cards": cards,
        "templates": templates,
        "results": results,
        "steps": v.get("description", ""),
        "prerequisites": prereq.strip(),
        "mana_needed": _get(v, "mana_needed", ""),
        "mana_value": _get(v, "mana_value_needed", 0) or 0,
        "popularity": v.get("popularity") or 0,
        "bracket": _get(v, "bracket_tag", ""),
        "url": COMBO_URL.format(id=v.get("id")),
        "missing": missing,
        "wins": any(w in r.lower() for r in results for w in WIN_WORDS),
    }


def _cache_dir() -> Path:
    base = os.environ.get("MTGSIM_HOME") or (Path.home() / ".mtgsim")
    return Path(base) / "combos"


def _post(body: dict, timeout: float) -> dict:
    req = urllib.request.Request(API, data=json.dumps(body).encode(), method="POST", headers={
        "Content-Type": "application/json", "Accept": "application/json",
        "User-Agent": "MTGDeckBuildingSimulation/0.1"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise ComboError(f"Commander Spellbook answered HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        raise ComboError(f"Couldn't reach Commander Spellbook ({e.reason}).") from e


def find_combos(main: list, commanders: list, use_cache: bool = True, timeout: float = 60,
                raw: dict | None = None) -> dict:
    """Look up combos for a deck. ``main`` and ``commanders`` are lists of card names
    (repeat a name for multiple copies). ``raw`` lets tests pass a saved API response."""
    body = {"main": [{"card": n, "quantity": q} for n, q in Counter(main).items()],
            "commanders": [{"card": n, "quantity": 1} for n in commanders]}
    if raw is None:
        key = hashlib.sha1(json.dumps(body, sort_keys=True).encode()).hexdigest()[:16]
        path = _cache_dir() / f"{key}.json"
        if use_cache and path.exists() and time.time() - path.stat().st_mtime < CACHE_DAYS * 86400:
            raw = json.loads(path.read_text(encoding="utf-8"))
        else:
            raw = _post(body, timeout)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(raw), encoding="utf-8")
    results = raw.get("results", raw)
    names = set(main) | set(commanders)
    included = [_variant(v, names) for v in _get(results, "included", [])]
    almost = [_variant(v, names) for v in _get(results, "almost_included", [])]
    included.sort(key=lambda c: (not c["wins"], -c["popularity"]))
    almost.sort(key=lambda c: (len(c["missing"]), not c["wins"], -c["popularity"]))
    adds = Counter()
    win_adds = Counter()
    for c in almost:
        if len(c["missing"]) == 1:
            adds[c["missing"][0]] += 1
            if c["wins"]:
                win_adds[c["missing"][0]] += 1
    return {
        "identity": _get(results, "identity", ""),
        "included": included,
        "almost": almost,
        "almost_other_colors": len(_get(results, "almost_included_by_adding_colors", []) or []),
        "best_additions": [{"card": n, "combos": k, "winning_combos": win_adds[n]} for n, k in adds.most_common(15)],
    }


def combos_for_deck(deck, use_cache: bool = True) -> dict:
    """Convenience wrapper for a LoadedDeck."""
    return find_combos([c.name for c in deck.cards], [c.name for c in deck.commanders], use_cache=use_cache)


def winning_combos(result: dict) -> list:
    """Combos in the deck that win the game, in the form the matchup simulator uses."""
    return [{"cards": c["cards"], "wins": True, "mana": int(c["mana_value"] or 0)}
            for c in result.get("included", []) if c["wins"] and not c["templates"]]
