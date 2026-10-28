from helpers import KROS, PROPAGANDA, basic, make

from mtgsim.archetypes import archetype
from mtgsim.engine import Creature, Game
from mtgsim.goldfish import run_goldfish


def _game(n_players=3, seed=1):
    decks = [archetype("stompy") for _ in range(n_players)]
    g = Game([(f"P{i}", d.cards, d.commanders, d.colors) for i, d in enumerate(decks)], seed=seed)
    for pl in g.players:
        pl.library = pl.library[:40]
    return g


def test_goldfish_is_deterministic_and_develops():
    deck = archetype("stompy")
    a = run_goldfish(deck, games=200, seed=3)
    b = run_goldfish(deck, games=200, seed=3)
    assert a["avg_score"] == b["avg_score"]
    lands = [t["lands"] for t in a["turns"]]
    assert lands == sorted(lands) and lands[-1] > 4


def test_attack_tax_is_charged():
    g = _game(2)
    me, opp = g.players
    me.permanents.append(make(*PROPAGANDA))
    bear = Creature(None, opp.idx, 5, 5)
    bear.sick = False
    opp.creatures.append(bear)
    assert g.attack_tax(me, bear) == 2
    opp.pool = [frozenset("G")] * 1
    plan, tax = g.plan_attacks(opp, budget=1)
    assert plan == []  # can't afford the tax
    opp.pool = [frozenset("G")] * 4
    plan, tax = g.plan_attacks(opp, budget=4)
    assert tax == 2 and plan[0][1] is me


def test_kros_goads_away_from_you():
    g = _game(3)
    me, a, b = g.players
    kros = make(*KROS)
    me.creatures.append(Creature(kros, me.idx, 2, 4))
    brute = Creature(None, a.idx, 5, 5)
    brute.sick = False
    a.creatures.append(brute)
    g.place_opp_counters(me, kros.params["opp_counters"][0])
    assert brute.counters["shield"] == 1 and me.idx in brute.goaded_by and brute.tapped
    brute.tapped = False
    plan, _ = g.plan_attacks(a, budget=0)
    assert plan and plan[0][1] is b  # goaded creature must attack someone other than you


def test_prevention_and_fog():
    g = _game(2)
    me, opp = g.players
    shield = make("Delaying Shield", "{3}{W}", "Enchantment",
                  "If damage would be dealt to you, put that many delay counters on Delaying Shield instead.\n"
                  "At the beginning of your upkeep, remove all delay counters from Delaying Shield. For each delay "
                  "counter removed this way, you lose 1 life unless you pay {1}{W}.")
    me.permanents.append(shield)
    assert g.damage_player(me, 5) == 0 and me.delay_counters == 5 and me.life == 40


def test_full_game_runs():
    deck = archetype("tokens")
    g = Game([(n, d.cards, d.commanders, d.colors) for n, d in
              [("A", deck), ("B", archetype("stompy")), ("C", archetype("voltron"))]], seed=4, verbose=True)
    g.run()
    assert g.events and g.round >= 1
    assert all(len(p.hand) <= 7 for p in g.players if p.alive)


def test_land_drops_with_basics():
    forest = basic("Forest", "G")
    g = Game([("solo", [forest] * 40, [], frozenset("G"))], seed=1, max_rounds=4, goldfish=True)
    g.run()
    assert len(g.players[0].lands) == 4
