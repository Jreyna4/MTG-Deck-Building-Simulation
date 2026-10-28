"""MTG Deck Building Simulation: opening-hand stats, mulligan advice, combos and matchups for
Commander decks.

The core package uses only the Python standard library so it can later be packaged for other
platforms (for example an Android app).
"""
from .cards import Card
from .deck import Deck, DeckEntry, load_deck, parse_deck
from .loader import LoadedDeck, load

__version__ = "0.1.0"
__all__ = ["Card", "Deck", "DeckEntry", "LoadedDeck", "load", "load_deck", "parse_deck", "__version__"]
