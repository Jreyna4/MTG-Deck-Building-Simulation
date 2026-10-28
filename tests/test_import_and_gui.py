"""Moxfield import, commander detection and a quick check that the window opens."""
import pytest

from mtgsim import moxfield
from mtgsim.deck import parse_deck
from mtgsim.loader import _guess_commander

# Shaped like Moxfield's deck JSON (older layout: boards at the top level, keyed by card name)
MOX_V2 = {
    "name": "Kros Fort",
    "commanders": {"Kros, Defense Contractor": {"quantity": 1, "card": {"name": "Kros, Defense Contractor",
                                                                        "set": "ncc", "cn": "7"}}},
    "mainboard": {
        "Sol Ring": {"quantity": 1, "card": {"name": "Sol Ring", "set": "fdc", "cn": "286"}},
        "Island": {"quantity": 11, "card": {"name": "Island"}},
        "Thaumatic Compass // Spires of Orazca": {"quantity": 1,
                                                  "card": {"name": "Thaumatic Compass // Spires of Orazca"}},
    },
    "sideboard": {},
}
# Newer layout: boards -> name -> cards
MOX_V3 = {"name": "New layout", "boards": {
    "commanders": {"count": 1, "cards": {"a1": {"quantity": 1, "card": {"name": "Kros, Defense Contractor"}}}},
    "mainboard": {"count": 2, "cards": {"b1": {"quantity": 2, "card": {"name": "Plains"}}}},
}}


def test_moxfield_links():
    assert moxfield.deck_id("https://www.moxfield.com/decks/AbC-12_x") == "AbC-12_x"
    assert moxfield.is_link("https://moxfield.com/decks/AbC12/primer")
    assert not moxfield.is_link("1 Sol Ring\n1 Island")
    assert moxfield.deck_id("1 Sol Ring") is None


def test_moxfield_json_layouts():
    d = moxfield.deck_from_json(MOX_V2)
    assert d.name == "Kros Fort"
    assert [c.name for c in d.commanders] == ["Kros, Defense Contractor"]
    assert d.main_count == 13
    assert next(e for e in d.main if e.name == "Sol Ring").set_code == "FDC"
    assert next(e for e in d.main if e.name.startswith("Thaumatic")).lookup_name == "Thaumatic Compass"
    d3 = moxfield.deck_from_json(MOX_V3)
    assert [c.name for c in d3.commanders] == ["Kros, Defense Contractor"] and d3.main_count == 2


def test_link_needs_permission(monkeypatch):
    monkeypatch.delenv("MTGSIM_MOXFIELD_USER_AGENT", raising=False)
    monkeypatch.setattr(moxfield.settings, "get", lambda key, default=None: None)
    with pytest.raises(moxfield.MoxfieldError, match="permission"):
        moxfield.fetch_deck("https://www.moxfield.com/decks/abc")


class _Card:
    def __init__(self, name, legendary):
        self.name, self.can_be_commander = name, legendary


def test_unmarked_commander_at_the_end_is_found():
    deck = parse_deck("1 Sol Ring\n10 Island\n\n1 Kros, Defense Contractor\n")
    assert not deck.commanders and deck.edge_blocks
    _guess_commander(deck, {"Sol Ring": _Card("Sol Ring", False), "Island": _Card("Island", False),
                            "Kros, Defense Contractor": _Card("Kros, Defense Contractor", True)})
    assert [c.name for c in deck.commanders] == ["Kros, Defense Contractor"]
    assert deck.main_count == 11


def test_unmarked_block_of_basics_is_left_alone():
    deck = parse_deck("1 Sol Ring\n\n10 Island\n10 Plains\n")
    _guess_commander(deck, {n: _Card(n, False) for n in ("Sol Ring", "Island", "Plains")})
    assert not deck.commanders and deck.main_count == 21


def test_change_commander_and_text_round_trip():
    deck = parse_deck("1 Sol Ring\n1 Lulu, Stern Guardian\nCOMMANDER:\n1 Kros, Defense Contractor\n")
    deck.change_commanders(["Lulu, Stern Guardian"])
    assert [c.name for c in deck.commanders] == ["Lulu, Stern Guardian"]
    assert {e.name for e in deck.main} == {"Sol Ring", "Kros, Defense Contractor"}
    again = parse_deck(deck.to_text())
    assert [c.name for c in again.commanders] == ["Lulu, Stern Guardian"] and again.main_count == 2


def test_window_opens():
    tk = pytest.importorskip("tkinter")
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip("no display")
    root.withdraw()
    try:
        from mtgsim.gui import HELP, App
        app = App(root)
        root.update()
        assert app.output_text().startswith("MTG DECK BUILDING SIMULATION")
        assert set("ircs123456hq") <= set(app.buttons)
        assert not app.buttons["1"].enabled or app.deck is None
        assert "Moxfield" in HELP
    finally:
        root.destroy()
