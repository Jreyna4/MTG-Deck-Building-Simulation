from helpers import KROS, PROPAGANDA, make


def test_attack_taxes():
    prop = make(*PROPAGANDA)
    assert "attack_tax" in prop.tags and prop.params["attack_tax"][0]["amount"] == 2
    sphere = make("Sphere of Safety", "{4}{W}", "Enchantment",
                  "Creatures can't attack you or planeswalkers you control unless their controller pays {X} for "
                  "each of those creatures, where X is the number of enchantments you control.")
    assert sphere.params["attack_tax"][0]["per"] == "enchantments"


def test_goad_engine():
    kros = make(*KROS)
    assert kros.params["goad_on_counters"]
    assert kros.params["opp_counters"][0]["timing"] == "upkeep"
    assert "goad" in kros.tags and "protection" not in kros.tags


def test_ramp_and_draw():
    sol = make("Sol Ring", "{1}", "Artifact", "{T}: Add {C}{C}.")
    assert {"ramp", "mana_rock"} <= sol.tags and len(sol.params["mana"]) == 2
    cultivate = make("Cultivate", "{2}{G}", "Sorcery",
                     "Search your library for up to two basic land cards, reveal those cards, put one onto the "
                     "battlefield tapped and the other into your hand, then shuffle.")
    ls = cultivate.params["land_search"][0]
    assert ls["to_bf"] == 1 and ls["to_hand"] == 1 and ls["tapped"]
    land_tax = make("Land Tax", "{W}", "Enchantment",
                    "At the beginning of your upkeep, if an opponent controls more lands than you, you may search "
                    "your library for up to three basic land cards, reveal them, put them into your hand, then "
                    "shuffle.")
    assert land_tax.params["land_search"][0] == {"to_bf": 0, "to_hand": 3, "tapped": False, "colors": [],
                                                 "timing": "upkeep", "cost": ""}
    divination = make("Divination", "{2}{U}", "Sorcery", "Draw two cards.")
    assert divination.params["draw_cast"] == 2


def test_interaction():
    swords = make("Swords to Plowshares", "{W}", "Instant",
                  "Exile target creature. Its controller gains life equal to its power.")
    assert "removal" in swords.tags and swords.params["removal"][0]["timing"] == "instant"
    wrath = make("Wrath of God", "{2}{W}{W}", "Sorcery", "Destroy all creatures. They can't be regenerated.")
    assert "board_wipe" in wrath.tags
    counter = make("Counterspell", "{U}{U}", "Instant", "Counter target spell.")
    assert "counterspell" in counter.tags
    fog = make("Holy Day", "{W}", "Instant", "Prevent all combat damage that would be dealt this turn.")
    assert "fog" in fog.tags
    pick = make("Pick Your Poison", "{G}", "Sorcery",
                "Choose one —\n• Each opponent sacrifices an artifact.\n• Each opponent sacrifices an enchantment.\n"
                "• Each opponent sacrifices a creature with flying.")
    assert "edict" in pick.tags and len(pick.params["removal"]) == 3


def test_creatures():
    angel = make("Serra Angel", "{3}{W}{W}", "Creature — Angel", "Flying, vigilance", 4, 4,
                 keywords=("Flying", "Vigilance"))
    assert angel.params["flying"] and angel.params["vigilance"] and "threat" in angel.tags
    maker = make("Squad Leader", "{2}{W}", "Creature — Human",
                 "When Squad Leader enters, create two 1/1 white Soldier creature tokens.", 2, 2)
    tok = maker.params["tokens"][0]
    assert tok["n"] == 2 and tok["timing"] == "etb"
