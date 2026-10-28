"""Combo parsing, mulligan advice and matchups (no internet needed)."""
from mtgsim.archetypes import archetype
from mtgsim.combos import find_combos, winning_combos
from mtgsim.handstats import opening_hand_stats, p_at_least
from mtgsim.matchup import card_impact, run_matchup
from mtgsim.mulligan import MulliganAdvisor, find_cards

# Shaped like Commander Spellbook's find-my-combos answer (camelCase JSON)
SPELLBOOK_ANSWER = {
    "count": 2, "next": None, "previous": None,
    "results": {
        "identity": "GU",
        "included": [{
            "id": "1-2", "uses": [{"card": {"name": "Card A"}, "quantity": 1, "mustBeCommander": False},
                                  {"card": {"name": "Card B"}, "quantity": 1, "mustBeCommander": False}],
            "requires": [], "produces": [{"feature": {"name": "Infinite damage"}, "quantity": 1}],
            "description": "Do the thing.", "manaNeeded": "{2}", "manaValueNeeded": 2,
            "easyPrerequisites": "All permanents on the battlefield.", "notablePrerequisites": "",
            "popularity": 100, "bracketTag": "C"}],
        "almostIncluded": [{
            "id": "3-4", "uses": [{"card": {"name": "Card A"}}, {"card": {"name": "Card C"}}],
            "requires": [], "produces": [{"feature": {"name": "Infinite mana"}}],
            "description": "", "popularity": 5}],
        "almostIncludedByAddingColors": [], "includedByChangingCommanders": [],
    },
}


def test_combo_parsing():
    res = find_combos(["Card A", "Card B", "Forest"], ["Commander X"], raw=SPELLBOOK_ANSWER)
    assert len(res["included"]) == 1 and res["included"][0]["wins"]
    assert res["included"][0]["url"].endswith("/combo/1-2/")
    assert res["almost"][0]["missing"] == ["Card C"]
    assert res["best_additions"][0]["card"] == "Card C"
    assert winning_combos(res) == [{"cards": ["Card A", "Card B"], "wins": True, "mana": 2}]


def test_hypergeometric():
    assert abs(p_at_least(1, 99, 7) - 7 / 99) < 1e-9
    assert p_at_least(34, 99, 7) > 0.95


def test_hand_stats():
    s = opening_hand_stats(archetype("tokens"), samples=2000)
    assert abs(sum(s["land_distribution"].values()) - 1) < 1e-9
    assert 2 < s["avg_lands_in_7"] < 3


def test_mulligan_advice():
    deck = archetype("stompy")
    adv = MulliganAdvisor(deck, train_hands=400, baseline_samples=300)
    bad = find_cards(deck, ["Forest", "Ancient Colossus", "Ancient Colossus", "Grove Titan", "Grove Titan",
                            "Grove Titan", "Wild Charger"])
    good = find_cards(deck, ["Forest", "Forest", "Mountain", "Mana Elf", "Ramp Spell", "Ridgeback Brute",
                             "Grove Titan"])
    assert adv.evaluate(bad, sims=60)["decision"] == "mulligan"
    assert adv.evaluate(good, sims=60)["decision"] == "keep"
    r = adv.evaluate(bad, mulligans_taken=2, sims=30)
    assert len(r["keep"]) == 6 and len(r["bottom"]) == 1


def test_matchup_and_card_impact():
    you = archetype("voltron")
    opps = [archetype("tokens"), archetype("stompy")]
    r = run_matchup(you, opps, games=20, seed=2)
    assert r["games"] == 20 and 0 <= r["win_rate"] <= 1
    imp = card_impact(you, opps, cards=["Battle Blade"], games=15, seed=2)
    row = imp["cards"][0]
    assert row["name"] == "Battle Blade" and 0 <= row["drawn_rate"] <= 1
