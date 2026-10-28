"""Goldfishing: playing a deck alone for a few turns to see how smoothly it develops.

The *score* of a goldfish game is the mana you put to use over the first five turns: mana spent
on spells, plus mana held open for instant-speed interaction you're holding. Hands that hit land
drops, ramp early and have things to cast score higher.
"""
from __future__ import annotations

import random
from collections import defaultdict

from .engine import Game, heuristic_mulligan
from .loader import LoadedDeck

SCORE_TURNS = 5


def play_goldfish(deck: LoadedDeck, seed: int, turns: int = 6, hand: list | None = None,
                  on_play: bool | None = None, library_order: list | None = None) -> dict:
    """Play one solo game. ``hand`` forces the opening hand (a list of Card objects from the deck)."""
    g = Game([(deck.name, deck.cards, deck.commanders, deck.colors)], seed=seed, max_rounds=turns,
             goldfish=True, first=0)
    rng = random.Random(seed * 7 + 1)
    if on_play is None:
        on_play = rng.random() < 0.25  # in a 4-player game you go first a quarter of the time
    g.first_player_skips_draw = on_play
    if library_order is not None:
        g.players[0].library = list(library_order)
    g.run(hands={0: hand} if hand is not None else None)
    log = g.log_turns
    opener = hand if hand is not None else None
    score = sum(t["spent"] + t["held"] for t in log[:SCORE_TURNS])
    return {"log": log, "score": score, "on_play": on_play, "opening": opener,
            "cmd_turn": g.players[0].cmd_cast_turn}


def run_goldfish(deck: LoadedDeck, games: int = 2000, turns: int = 6, seed: int = 1, mulligans: bool = True,
                 policy=None) -> dict:
    """Play many solo games and summarize how the deck develops. With ``mulligans`` the player
    mulligans bad hands (London rules, first mulligan free) using ``policy`` or a simple rule."""
    per_turn = defaultdict(lambda: defaultdict(float))
    cmd_turns = defaultdict(int)
    scores = []
    card_in_opener = defaultdict(list)
    card_drawn_early = defaultdict(int)
    card_stuck = defaultdict(int)
    names = {c.name for c in deck.cards}
    mull_count = defaultdict(int)
    for i in range(games):
        g = Game([(deck.name, deck.cards, deck.commanders, deck.colors)], seed=seed * 100003 + i,
                 max_rounds=turns, goldfish=True, first=0)
        on_play = g.rng.random() < 0.25
        g.first_player_skips_draw = on_play
        g.setup()
        if mulligans:
            g.london_mulligan(g.players[0], policy or heuristic_mulligan)
            mull_count[g.players[0].mulligans] += 1
        opener = [c.name for c in g.players[0].hand]
        g.run(setup=False)
        log = g.log_turns
        score = sum(t["spent"] + t["held"] for t in log[:SCORE_TURNS])
        scores.append(score)
        for t in log:
            row = per_turn[t["turn"]]
            row["lands"] += t["lands"]
            row["mana"] += t["mana"]
            row["used"] += t["spent"] + t["held"]
            row["defense"] += t["defense"]
            row["games"] += 1
        cmd_turns[g.players[0].cmd_cast_turn or 0] += 1
        for n in set(opener):
            card_in_opener[n].append(score)
        # cards seen by turn 3 that were still sitting in hand at the end of turn 5
        if len(log) >= 5:
            seen_t3 = set(opener) | set(log[2]["hand"])
            end_t5 = set(log[4]["hand"])
            for n in seen_t3:
                card_drawn_early[n] += 1
                if n in end_t5:
                    card_stuck[n] += 1
    avg = sum(scores) / len(scores)
    turns_out = []
    for t in sorted(per_turn):
        row = per_turn[t]
        n = row["games"]
        turns_out.append({"turn": t, "lands": row["lands"] / n, "mana": row["mana"] / n,
                          "used": row["used"] / n, "defense": row["defense"] / n})
    cards = []
    by_name = {c.name: c for c in deck.cards}
    for n in names:
        s = card_in_opener.get(n, [])
        if not s:
            continue
        others = (avg * len(scores) - sum(s)) / max(1, len(scores) - len(s))
        cards.append({
            "name": n, "in_opener": len(s), "opener_avg": sum(s) / len(s),
            "lift": sum(s) / len(s) - others,
            "stuck_rate": card_stuck[n] / card_drawn_early[n] if card_drawn_early[n] else 0.0,
            "is_land": by_name[n].is_land, "mana_value": by_name[n].mana_value,
        })
    cards.sort(key=lambda r: r["lift"], reverse=True)
    sorted_scores = sorted(scores)
    return {
        "games": games, "turns": turns_out, "avg_score": avg,
        "score_percentiles": {p: sorted_scores[int(p / 100 * (len(scores) - 1))] for p in (10, 25, 50, 75, 90)},
        "commander_turn": dict(sorted(cmd_turns.items())), "cards": cards,
        "mulligans": dict(sorted(mull_count.items())) if mulligans else None,
    }

