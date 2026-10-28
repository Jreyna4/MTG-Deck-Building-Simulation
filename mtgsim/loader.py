"""Load a deck list into tagged Card objects in one call."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from .cards import Card
from .deck import Deck, load_deck, parse_deck
from .mana import COLORS
from .scryfall import CardCache, fetch_cards
from .tagger import apply_override, load_overrides, tag_card


@dataclass
class LoadedDeck:
    deck: Deck
    commanders: list
    cards: list  # the main deck, one entry per physical card (basics repeat)
    colors: frozenset
    not_found: list = field(default_factory=list)

    @property
    def name(self) -> str:
        return self.deck.name

    @property
    def warnings(self) -> list:
        return self.deck.warnings

    def unique(self) -> list:
        """(card, copies) for each different card in the main deck, in list order."""
        counts = Counter(c.name for c in self.cards)
        seen, out = set(), []
        for c in self.cards:
            if c.name not in seen:
                seen.add(c.name)
                out.append((c, counts[c.name]))
        return out

    @property
    def land_count(self) -> int:
        return sum(1 for c in self.cards if c.is_land)


def build_loaded_deck(deck: Deck, cache: CardCache | None = None, offline: bool = False,
                      overrides_path: str | Path | None = None, progress=None) -> LoadedDeck:
    names = [e.lookup_name for e in deck.all_entries()]
    found, not_found = fetch_cards(names, cache=cache, offline=offline, progress=progress)
    commanders = [found[e.lookup_name] for e in deck.commanders if e.lookup_name in found]
    identity = set()
    for c in commanders:
        identity |= set(c.color_identity)
    if not commanders:
        for e in deck.main:
            if e.lookup_name in found:
                identity |= set(found[e.lookup_name].color_identity)
    colors = frozenset(c for c in identity if c in COLORS) or frozenset(COLORS)
    overrides = load_overrides(overrides_path)
    for card in set(found.values()):
        tag_card(card, colors)
        apply_override(card, overrides)
    cards = []
    for e in deck.main:
        if e.lookup_name in found:
            cards.extend([found[e.lookup_name]] * e.quantity)
    return LoadedDeck(deck=deck, commanders=commanders, cards=cards, colors=colors, not_found=not_found)


def load(path_or_text: str | Path, commander: str | None = None, cache_path: str | Path | None = None,
         offline: bool = False, overrides_path: str | Path | None = None, progress=None) -> LoadedDeck:
    """Load a deck from a file path or from pasted deck-list text."""
    if isinstance(path_or_text, Path) or ("\n" not in str(path_or_text) and Path(str(path_or_text)).exists()):
        deck = load_deck(path_or_text, commander=commander)
    else:
        deck = parse_deck(str(path_or_text), commander=commander)
    cache = CardCache(cache_path) if cache_path else CardCache()
    return build_loaded_deck(deck, cache=cache, offline=offline, overrides_path=overrides_path, progress=progress)
