"""Matchup simulator: your deck against one to three opponents.

Plays many multiplayer games with the engine and reports how often you win and survive, how much
damage you take, and how much your defenses blocked. ``card_impact`` then measures each card by
playing the same games again with that card turned into a blank ("ablation"), so a card's
defense score is the damage it actually saved you, not a guess.
"""
from __future__ import annotations

from collections import defaultdict
from math import sqrt

from .cards import Card
from .engine import Game


def _entry(deck):
    return (deck.name, deck.cards, deck.commanders, deck.colors)


def _blank_like(card: Card) -> Card:
    return Card(name=f"(blank) {card.name}", type_line="Sorcery", synthetic=True)


def play_games(you, opponents: list, games: int = 200, seed: int = 1, max_rounds: int = 15,
               combos: list | None = None, swap: dict | None = None, your_mulligan=None) -> list:
    """Play ``games`` games and return one result dict per game, from your point of view.
    ``swap`` replaces cards in your deck by name (used for ablation)."""
    cards = you.cards
    if swap:
        cards = [swap.get(c.name, c) for c in cards]
    decks = [(you.name, cards, you.commanders, you.colors)] + [_entry(o) for o in opponents]
    out = []
    for i in range(games):
        g = Game(decks, seed=seed * 1_000_003 + i, max_rounds=max_rounds, combos=combos,
                 policies={0: your_mulligan} if your_mulligan else None)
        g.run()
        me = g.players[0]
        out.append({
            "won": g.winner == 0,
            "winner": g.winner,
            "alive": me.alive,
            "died_round": me.died_round,
            "damage_taken": me.damage_taken,
            "damage_prevented": me.damage_prevented,
            "tax_collected": me.tax_collected,
            "times_attacked": me.times_attacked,
            "attackers_faced": me.attackers_faced,
            "life": me.life if me.alive else 0,
            "rounds": g.round,
            "seen": me.cards_seen,
            "first": g.order[0].idx == 0,
            "mulligans": me.mulligans,
        })
    return out


def summarize(results: list, n_opponents: int) -> dict:
    n = len(results)
    won = sum(r["won"] for r in results)
    alive = sum(r["alive"] for r in results)
    deaths = [r["died_round"] for r in results if not r["alive"] and r["died_round"]]
    others = defaultdict(int)
    for r in results:
        if r["winner"] is None:
            others["no winner (time limit)"] += 1
        elif r["winner"] != 0:
            others[f"opponent {r['winner']}"] += 1
    return {
        "games": n,
        "win_rate": won / n,
        "fair_share": 1 / (n_opponents + 1),
        "survival_rate": alive / n,
        "avg_death_round": sum(deaths) / len(deaths) if deaths else None,
        "avg_damage_taken": sum(r["damage_taken"] for r in results) / n,
        "avg_damage_prevented": sum(r["damage_prevented"] for r in results) / n,
        "avg_tax_collected": sum(r["tax_collected"] for r in results) / n,
        "avg_times_attacked": sum(r["times_attacked"] for r in results) / n,
        "avg_attackers_faced": sum(r["attackers_faced"] for r in results) / n,
        "avg_mulligans": sum(r.get("mulligans", 0) for r in results) / n,
        "outcomes": dict(others),
    }


def run_matchup(you, opponents: list, games: int = 200, seed: int = 1, max_rounds: int = 15,
                combos: list | None = None, your_mulligan=None) -> dict:
    results = play_games(you, opponents, games, seed, max_rounds, combos, your_mulligan=your_mulligan)
    summary = summarize(results, len(opponents))
    summary["opponents"] = [o.name for o in opponents]
    summary["outcomes"] = {(f"{opponents[int(k.split()[1]) - 1].name} won" if k.startswith("opponent ") else k): v
                           for k, v in summary["outcomes"].items()}
    if summary["win_rate"]:
        summary["outcomes"] = {"you won": round(summary["win_rate"] * len(results)), **summary["outcomes"]}
    summary["card_presence"] = presence_stats(you, results)
    return summary


def presence_stats(you, results: list) -> list:
    """Quick look (no extra games): results when you drew a card vs when you didn't.
    This shows correlation, not cause; ``card_impact`` measures cause."""
    rows = []
    for card, _ in you.unique():
        if card.is_land:
            continue
        with_c = [r for r in results if card.name in r["seen"]]
        without = [r for r in results if card.name not in r["seen"]]
        if len(with_c) < 10 or len(without) < 10:
            continue
        rows.append({
            "name": card.name, "drawn_in": len(with_c) / len(results),
            "survival_with": sum(r["alive"] for r in with_c) / len(with_c),
            "survival_without": sum(r["alive"] for r in without) / len(without),
            "damage_with": sum(r["damage_taken"] for r in with_c) / len(with_c),
            "damage_without": sum(r["damage_taken"] for r in without) / len(without),
        })
    return rows


def _ablate(args):
    you, opponents, name, games, seed, max_rounds, combos, your_mulligan = args
    card = next(c for c in you.cards if c.name == name)
    return name, play_games(you, opponents, games, seed, max_rounds, combos, swap={name: _blank_like(card)},
                            your_mulligan=your_mulligan)


def card_impact(you, opponents: list, cards: list | None = None, games: int = 150, seed: int = 1,
                max_rounds: int = 15, combos: list | None = None, progress=None, your_mulligan=None,
                workers: int = 1) -> dict:
    """Measure each card by replaying the same games with it blanked out.

    Each card gets three numbers, measured over the games where you drew it:
      defense_score  = damage you take without it minus with it (positive = keeps damage off you)
      survival_delta = how much more often you're alive at the end with it
      win_delta      = how much more often you win with it
    The "_noise" values are one standard error; differences smaller than about twice the noise
    could be luck. More games shrink the noise.
    """
    base = play_games(you, opponents, games, seed, max_rounds, combos, your_mulligan=your_mulligan)
    base_sum = summarize(base, len(opponents))
    if cards is None:
        cards = [c.name for c, _ in you.unique() if not c.is_land]
    cards = [n for n in cards if any(c.name == n for c in you.cards)]
    jobs = [(you, opponents, n, games, seed, max_rounds, combos, your_mulligan) for n in cards]
    results = {}
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor
        with ProcessPoolExecutor(workers) as ex:
            for i, (name, alt) in enumerate(ex.map(_ablate, jobs)):
                results[name] = alt
                if progress:
                    progress(i + 1, len(cards), name)
    else:
        for i, job in enumerate(jobs):
            if progress:
                progress(i + 1, len(cards), job[2])
            name, alt = _ablate(job)
            results[name] = alt
    rows = []
    for name in cards:
        alt = results[name]
        card = next(c for c in you.cards if c.name == name)
        drawn = [i for i, b in enumerate(base) if name in b["seen"]]
        dmg = [alt[i]["damage_taken"] - base[i]["damage_taken"] for i in drawn]
        surv = [base[i]["alive"] - alt[i]["alive"] for i in drawn]
        win = [base[i]["won"] - alt[i]["won"] for i in drawn]
        rows.append({
            "name": name,
            "tags": sorted(card.tags - {"creature"}),
            "drawn_rate": len(drawn) / len(base),
            "defense_score": _mean(dmg), "defense_noise": _stderr(dmg),
            "survival_delta": _mean(surv), "survival_noise": _stderr(surv),
            "win_delta": _mean(win), "win_noise": _stderr(win),
        })
    rows.sort(key=lambda r: (r["win_delta"] + r["survival_delta"], r["defense_score"]), reverse=True)
    return {"baseline": base_sum, "cards": rows, "games_per_card": games}


def _mean(xs):
    return sum(xs) / len(xs) if xs else 0.0


def _stderr(xs):
    if len(xs) < 2:
        return 0.0
    m = _mean(xs)
    var = sum((x - m) ** 2 for x in xs) / (len(xs) - 1)
    return sqrt(var / len(xs))
