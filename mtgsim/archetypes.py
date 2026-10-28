"""Built-in practice opponents for the matchup simulator.

These are made-up "generic" cards (marked synthetic) that stand in for common Commander
strategies, so you can test a deck without typing in opponents' lists. Each one uses ordinary
Oracle wording, so the same tagger and engine handle them as real cards.
"""
from __future__ import annotations

from collections import Counter

from .cards import Card
from .mana import parse_mana_cost
from .tagger import tag_card


def _card(name, cost, type_line, text="", p=None, t=None, keywords=()):
    return Card(name=name, mana_cost=cost, cmc=parse_mana_cost(cost).value(), type_line=type_line,
                oracle_text=text, power=None if p is None else str(p), toughness=None if t is None else str(t),
                keywords=list(keywords), synthetic=True)


def _basic(name):
    return Card(name=name, type_line=f"Basic Land — {name}", synthetic=True)


RAMP_SPELL = ("Ramp Spell", "{1}{G}", "Sorcery",
              "Search your library for a basic land card, put that card onto the battlefield tapped, then shuffle.")
MANA_ROCK = ("Mana Rock", "{2}", "Artifact", "{T}: Add one mana of any color.")
DRAW_TWO = ("Draw Two", "{2}{U}", "Sorcery", "Draw two cards.")
ART_ENCH_REMOVAL = ("Disenchant Effect", "{1}{W}", "Instant", "Destroy target artifact or enchantment.")


def _spec_tokens():
    cmdr = _card("Token Marshal (sim)", "{2}{G}{W}", "Legendary Creature — Human Soldier",
                 "At the beginning of your end step, create two 1/1 white Soldier creature tokens.", 3, 3)
    cards = [(17, _basic("Forest")), (18, _basic("Plains")),
             (4, _card(*RAMP_SPELL)), (3, _card(*MANA_ROCK)),
             (3, _card("Mana Elf", "{G}", "Creature — Elf Druid", "{T}: Add {G}.", 1, 1)),
             (8, _card("Squad Leader", "{2}{W}", "Creature — Human Soldier",
                       "When Squad Leader enters, create two 1/1 white Soldier creature tokens.", 2, 2)),
             (5, _card("Rally the Troops", "{3}{W}", "Sorcery", "Create four 1/1 white Soldier creature tokens.")),
             (5, _card("Saproling Grove", "{3}{G}", "Enchantment",
                       "At the beginning of your upkeep, create a 1/1 green Saproling creature token.")),
             (5, _card("Battle Hymn", "{2}{W}", "Enchantment", "Creatures you control get +1/+1.")),
             (4, _card("Plan Ahead", "{2}{G}", "Sorcery", "Draw two cards.")),
             (4, _card("Inspiring Banner", "{3}", "Artifact", "At the beginning of your upkeep, draw a card.")),
             (4, _card("Exile Strike", "{1}{W}", "Instant", "Exile target creature.")),
             (3, _card("Cleanse", "{1}{G}", "Instant", "Destroy target artifact or enchantment.")),
             (8, _card("Vanguard Knight", "{1}{W}", "Creature — Human Knight", "", 2, 2)),
             (8, _card("Trained Ranger", "{2}{G}", "Creature — Elf Ranger", "", 3, 3))]
    return "Tokens (sim)", cmdr, cards


def _spec_stompy():
    cmdr = _card("Thunder Beast (sim)", "{3}{R}{G}", "Legendary Creature — Beast",
                 "Trample, haste", 6, 6, keywords=("Trample", "Haste"))
    cards = [(18, _basic("Forest")), (18, _basic("Mountain")),
             (6, _card(*RAMP_SPELL)), (4, _card(*MANA_ROCK)),
             (4, _card("Mana Elf", "{G}", "Creature — Elf Druid", "{T}: Add {G}.", 1, 1)),
             (8, _card("Ridgeback Brute", "{2}{G}", "Creature — Beast", "Trample", 3, 3, keywords=("Trample",))),
             (7, _card("Wild Charger", "{2}{R}{R}", "Creature — Boar", "Haste", 4, 4, keywords=("Haste",))),
             (8, _card("Grove Titan", "{3}{G}{G}", "Creature — Beast", "Trample", 5, 5, keywords=("Trample",))),
             (6, _card("Ancient Colossus", "{5}{G}{G}", "Creature — Beast", "Trample", 7, 7, keywords=("Trample",))),
             (6, _card("Big Draw", "{2}{G}", "Sorcery", "Draw two cards.")),
             (2, _card("Beast Banner", "{3}", "Artifact", "At the beginning of your upkeep, draw a card.")),
             (4, _card("Crushing Blow", "{2}{G}", "Sorcery", "Destroy target creature.")),
             (4, _card("Nature's Claim Effect", "{G}", "Instant", "Destroy target artifact or enchantment.")),
             (4, _card("Fight Song", "{2}{R}", "Enchantment", "Creatures you control get +2/+0."))]
    return "Stompy (sim)", cmdr, cards


def _spec_voltron():
    cmdr = _card("Blade Champion (sim)", "{1}{W}{U}", "Legendary Creature — Human Knight",
                 "Flying", 3, 3, keywords=("Flying",))
    cards = [(18, _basic("Plains")), (18, _basic("Island")),
             (6, _card(*MANA_ROCK)),
             (4, _card("Sky Rock", "{1}", "Artifact", "{T}: Add {C}.")),
             (10, _card("Battle Blade", "{2}", "Artifact — Equipment",
                        "Equipped creature gets +3/+0.\nEquip {2}")),
             (6, _card("Holy Aura", "{1}{W}", "Enchantment — Aura",
                       "Enchant creature\nEnchanted creature gets +2/+2 and has flying.")),
             (8, _card("Shield Up", "{W}", "Instant",
                       "Target creature you control gains hexproof and indestructible until end of turn.")),
             (4, _card("Negate Spell", "{U}{U}", "Instant", "Counter target spell.")),
             (8, _card(*DRAW_TWO)),
             (3, _card("Exile Strike", "{1}{W}", "Instant", "Exile target creature.")),
             (3, _card(*ART_ENCH_REMOVAL)),
             (11, _card("Sky Squire", "{1}{U}", "Creature — Human Soldier", "Flying", 2, 2, keywords=("Flying",)))]
    return "Voltron (sim)", cmdr, cards


ARCHETYPES = {"tokens": _spec_tokens, "stompy": _spec_stompy, "voltron": _spec_voltron}


class SimDeck:
    """A built-in opponent deck, shaped like LoadedDeck for the simulator."""

    def __init__(self, name, commander, entries):
        self.name = name
        self.commanders = [commander]
        colors = set()
        for c in [commander] + [c for _, c in entries]:
            colors |= parse_mana_cost(c.mana_cost).colors
        self.colors = frozenset(colors)
        for c in [commander] + [c for _, c in entries]:
            tag_card(c, self.colors)
        self.cards = [c for n, c in entries for _ in range(n)]
        self.not_found = []

    @property
    def land_count(self):
        return sum(1 for c in self.cards if c.is_land)

    def unique(self) -> list:
        counts = Counter(c.name for c in self.cards)
        seen, out = set(), []
        for c in self.cards:
            if c.name not in seen:
                seen.add(c.name)
                out.append((c, counts[c.name]))
        return out


def archetype(key: str) -> SimDeck:
    key = key.lower().strip()
    if key not in ARCHETYPES:
        raise KeyError(f"Unknown practice opponent {key!r}. Choose from: {', '.join(ARCHETYPES)}")
    name, cmdr, entries = ARCHETYPES[key]()
    return SimDeck(name, cmdr, entries)
