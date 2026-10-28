"""Importing decks from Moxfield.

Moxfield doesn't have a public API, and its terms don't allow other programs to download decks
without permission. So there are two ways to bring a Moxfield deck in:

1. **Paste the export (always works).** On Moxfield, open the deck and choose More > Export,
   copy the plain text, and paste it into the program. The normal deck parser reads it.
2. **Paste the link (only with permission).** If Moxfield support (support@moxfield.com) gives
   you an approved API user agent, save it in the window's Moxfield settings (or set the
   ``MTGSIM_MOXFIELD_USER_AGENT`` environment variable) and deck links download directly.
"""
from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.request

from . import settings
from .deck import Deck, DeckEntry

DECK_LINK_RE = re.compile(r"moxfield\.com/decks/([A-Za-z0-9_-]+)", re.I)
API = "https://api.moxfield.com/v2/decks/all/{id}"
EXPORT_HELP = "On Moxfield, open the deck, choose More > Export, copy the plain text and paste it here."
NEEDS_PERMISSION = ("Moxfield doesn't let other programs download decks without its permission, so paste the "
                    "exported list instead. " + EXPORT_HELP + " (If Moxfield support gives you an approved API "
                    "user agent, add it in Moxfield settings and links will work.)")


class MoxfieldError(RuntimeError):
    pass


def deck_id(text: str) -> str | None:
    """The deck's ID from a Moxfield link, or None if the text isn't a Moxfield deck link."""
    m = DECK_LINK_RE.search(text.strip())
    return m.group(1) if m else None


def is_link(text: str) -> bool:
    t = text.strip()
    return "\n" not in t and deck_id(t) is not None


def user_agent() -> str | None:
    return os.environ.get("MTGSIM_MOXFIELD_USER_AGENT") or settings.get("moxfield_user_agent")


def fetch_deck(link: str, agent: str | None = None, timeout: float = 30) -> Deck:
    """Download a deck from a Moxfield link. Needs a user agent approved by Moxfield."""
    did = deck_id(link)
    if not did:
        raise MoxfieldError("That doesn't look like a Moxfield deck link (moxfield.com/decks/...).")
    agent = agent or user_agent()
    if not agent:
        raise MoxfieldError(NEEDS_PERMISSION)
    req = urllib.request.Request(API.format(id=did), headers={"User-Agent": agent, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise MoxfieldError("Moxfield couldn't find that deck. Check the link, and that the deck is public "
                                "or unlisted (not private).") from e
        if e.code in (401, 403, 429):
            raise MoxfieldError(f"Moxfield refused the download (HTTP {e.code}). Check the user agent Moxfield "
                                f"gave you, or paste the exported list instead. {EXPORT_HELP}") from e
        raise MoxfieldError(f"Moxfield answered HTTP {e.code}.") from e
    except urllib.error.URLError as e:
        raise MoxfieldError(f"Couldn't reach Moxfield ({e.reason}).") from e
    return deck_from_json(data)


def deck_from_json(data: dict) -> Deck:
    """Turn Moxfield's deck JSON into a Deck. Handles both the older layout (boards at the top
    level, keyed by card name) and the newer one (``boards: {mainboard: {cards: {...}}}``)."""
    boards = data.get("boards")

    def board(name: str) -> list:
        if isinstance(boards, dict):
            b = boards.get(name) or {}
            items = b.get("cards", b) if isinstance(b, dict) else b
        else:
            items = data.get(name) or {}
        return list(items.values()) if isinstance(items, dict) else list(items or [])

    def entries(name: str) -> list:
        out = []
        for item in board(name):
            if not isinstance(item, dict):
                continue
            for one in item.get("printingData") or [item]:
                card = one.get("card") or {}
                card_name = card.get("name") or one.get("name")
                if not card_name:
                    continue
                out.append(DeckEntry(name=card_name, quantity=int(one.get("quantity") or 1),
                                     set_code=(card.get("set") or "").upper() or None,
                                     collector_number=card.get("cn"),
                                     foil=bool(one.get("isFoil") or one.get("finish") in ("foil", "etched"))))
        return out

    deck = Deck(name=data.get("name") or "Moxfield deck")
    deck.commanders = entries("commanders")
    deck.main = entries("mainboard")
    deck.sideboard = entries("sideboard") + entries("companions")
    return deck
