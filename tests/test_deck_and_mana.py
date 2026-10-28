from mtgsim.deck import parse_deck
from mtgsim.mana import choose_payment, land_sources, parse_mana_cost

MOXFIELD = """1 Sol Ring (FDC) 286
1x Swords to Plowshares (FRC) 37 *F*
10 Plains (MSH) 434 *F*
1 Thaumatic Compass / Spires of Orazca (XLN) 249
1 Evolution Sage (PLST) WAR-159
1 Tidus, Yuna's Guardian

COMMANDER:
1 Kros, Defense Contractor (NCC) 7 *F*
"""


def test_parse_moxfield_export():
    deck = parse_deck(MOXFIELD)
    assert [c.name for c in deck.commanders] == ["Kros, Defense Contractor"]
    names = {e.name: e for e in deck.main}
    assert names["Plains"].quantity == 10
    assert names["Swords to Plowshares"].foil
    assert names["Sol Ring"].set_code == "FDC" and names["Sol Ring"].collector_number == "286"
    assert names["Evolution Sage"].collector_number == "WAR-159"
    assert names["Thaumatic Compass / Spires of Orazca"].lookup_name == "Thaumatic Compass"
    assert deck.main_count == 15


def test_archidekt_commander_marker_and_commander_argument():
    deck = parse_deck("1x Sol Ring (c21) 263 [Ramp]\n1x Atraxa, Praetors' Voice (2x2) 190 [Commander{top}]")
    assert [c.name for c in deck.commanders] == ["Atraxa, Praetors' Voice"]
    deck = parse_deck("1 Sol Ring\n1 Kros, Defense Contractor", commander="Kros, Defense Contractor")
    assert [c.name for c in deck.commanders] == ["Kros, Defense Contractor"]
    assert [e.name for e in deck.main] == ["Sol Ring"]


def test_arena_sections_and_warnings():
    deck = parse_deck("About\nName Test Deck\n\nCommander\n1 Kros, Defense Contractor\n\nDeck\n2 Island\n")
    assert deck.name == "Test Deck"
    assert deck.commanders[0].name == "Kros, Defense Contractor"
    assert any("100" in w for w in deck.warnings)


def test_mana_costs():
    c = parse_mana_cost("{2}{G}{W/U}{X}")
    assert c.generic == 2 and c.x_count == 1 and len(c.pips) == 2
    assert c.value() == 4 and c.value(x=3) == 7
    assert parse_mana_cost("{GW}{GW}").value() == 2  # Forge-style hybrid


def test_payment_matches_colors():
    w, u, g, c = frozenset("W"), frozenset("U"), frozenset("G"), frozenset("C")
    kros = parse_mana_cost("{1}{G}{W}{U}")
    assert choose_payment(kros, [w, u, g, c]) is not None
    assert choose_payment(kros, [w, u, c, c]) is None  # no green
    # the flexible source should be saved for the colored pip it is needed for
    assert choose_payment(parse_mana_cost("{1}{U}"), [frozenset("UG"), c]) is not None


def test_land_sources():
    colors = frozenset("GWU")
    assert land_sources("Basic Land — Forest", "", None, colors) == [frozenset("G")]
    assert land_sources("Land", "{T}: Add {W} or {U}.", None, colors) == [frozenset("WU")]
    assert land_sources("Land", "{T}: Add {C}.", None, colors) == [frozenset("C")]
