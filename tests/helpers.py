"""Small helpers to build cards without internet access."""
from mtgsim.cards import Card
from mtgsim.mana import parse_mana_cost
from mtgsim.tagger import tag_card


def make(name, cost="", type_line="Sorcery", text="", p=None, t=None, keywords=(), colors="WUBRG"):
    card = Card(name=name, mana_cost=cost, cmc=parse_mana_cost(cost).value(), type_line=type_line,
                oracle_text=text, power=None if p is None else str(p), toughness=None if t is None else str(t),
                keywords=list(keywords))
    return tag_card(card, frozenset(colors))


def basic(name, colors="WUBRG"):
    return tag_card(Card(name=name, type_line=f"Basic Land — {name}"), frozenset(colors))


PROPAGANDA = ("Propaganda", "{2}{U}", "Enchantment",
              "Creatures can't attack you unless their controller pays {2} for each creature they control "
              "that's attacking you.")
KROS = ("Kros, Defense Contractor", "{1}{G}{W}{U}", "Legendary Creature — Cat Advisor",
        "At the beginning of your upkeep, put a shield counter on target creature an opponent controls.\n"
        "Whenever you put one or more counters on a creature you don't control, tap that creature and goad it. "
        "It gains trample until your next turn. (Until your next turn, that creature attacks each combat if "
        "able and attacks a player other than you if able.)", 2, 4)
