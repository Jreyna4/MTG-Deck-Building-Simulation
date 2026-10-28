"""Mulligan advisor.

1. Train: play a few thousand goldfish games from random opening hands and fit a model that
   predicts the goldfish score from features of the hand.
2. Baseline: use the model to work out what a mulligan is worth on average at each step
   (Commander's first mulligan is free; after that you put cards on the bottom, London style).
3. Advise: goldfish *your* hand a few hundred times. Keep it if it beats the value of mulliganing.
"""
from __future__ import annotations

import random

from .cards import Card
from .engine import Game
from .goldfish import SCORE_TURNS
from .loader import LoadedDeck
from .model import LinearModel

FEATURES = ["0-1 lands", "2 lands", "3 lands", "4 lands", "5 lands", "6+ lands", "ramp", "cheap ramp",
            "cheap plays", "3-4 drops", "5+ drops", "card draw", "colors covered", "castable by turn 3",
            "few lands but ramp", "cards in hand"]
MIN_HAND = 4


def bottom_count(mulligans: int, free_first: bool = True) -> int:
    """Cards to put on the bottom after taking ``mulligans`` mulligans."""
    return max(0, mulligans - 1) if free_first else mulligans


def _sources(card: Card) -> list:
    return card.params.get("mana", []) if (card.is_land or "mana" in card.params) else []


def hand_features(hand: list, deck: LoadedDeck) -> list:
    lands = [c for c in hand if c.is_land]
    spells = [c for c in hand if not c.is_land]
    nl = len(lands)
    ramp = [c for c in spells if "ramp" in c.tags]
    colors = set()
    for c in lands + [c for c in spells if "mana" in c.params]:
        for u in _sources(c):
            colors |= u
    deck_colors = set(deck.colors)
    covered = len(colors & deck_colors) / max(1, len(deck_colors))
    mana_by_t3 = min(nl, 3) + sum(1 for c in ramp if c.cmc <= 2)
    castable = 0
    for c in spells:
        if c.cmc <= mana_by_t3 and c.cost.colors <= colors:
            castable += 1
    return [
        float(nl <= 1), float(nl == 2), float(nl == 3), float(nl == 4), float(nl == 5), float(nl >= 6),
        min(len(ramp), 3), min(sum(1 for c in ramp if c.cmc <= 2), 2),
        min(sum(1 for c in spells if c.cmc <= 2 and "ramp" not in c.tags), 3),
        min(sum(1 for c in spells if 3 <= c.cmc <= 4), 3),
        min(sum(1 for c in spells if c.cmc >= 5), 3),
        min(sum(1 for c in spells if c.tags & {"draw", "land_tutor"}), 2),
        covered, min(castable, 4), float(nl <= 2) * min(len(ramp), 2), len(hand),
    ]


def goldfish_hand(deck: LoadedDeck, hand: list, bottom: list, seed: int, on_play: bool | None = None) -> float:
    """Goldfish score of a kept hand (``bottom`` = cards put on the bottom of the library)."""
    g = Game([(deck.name, deck.cards, deck.commanders, deck.colors)], seed=seed, max_rounds=SCORE_TURNS,
             goldfish=True, first=0)
    if on_play is None:
        on_play = g.rng.random() < 0.25
    g.first_player_skips_draw = on_play
    pl = g.players[0]
    for c in hand + bottom:
        pl.library.remove(c)
    for c in bottom:
        pl.library.insert(0, c)
    pl.hand = list(hand)
    g.run(setup=False)
    return sum(t["spent"] + t["held"] for t in g.log_turns[:SCORE_TURNS])


def _heuristic_bottom(hand: list, n: int) -> tuple:
    """Simple bottoming rule used while training (before the model exists)."""
    hand = list(hand)
    out = []
    for _ in range(n):
        lands = [c for c in hand if c.is_land]
        spells = [c for c in hand if not c.is_land]
        if len(lands) > 3 or not spells:
            pick = lands[-1]
        else:
            pick = max(spells, key=lambda c: c.cmc)
        hand.remove(pick)
        out.append(pick)
    return hand, out


class MulliganAdvisor:
    def __init__(self, deck: LoadedDeck, train_hands: int = 3000, seed: int = 7, free_first: bool = True,
                 baseline_samples: int = 1500):
        self.deck = deck
        self.free_first = free_first
        self.rng = random.Random(seed)
        self.model = LinearModel(FEATURES)
        self._train(train_hands, seed)
        self._baseline(baseline_samples)

    # ---- training -----------------------------------------------------------------------
    def _train(self, n: int, seed: int) -> None:
        rows, ys = [], []
        for i in range(n):
            seven = self.rng.sample(self.deck.cards, 7)
            r = self.rng.random()
            n_bottom = 0 if r < 0.6 else 1 if r < 0.85 else 2
            hand, bottom = _heuristic_bottom(seven, n_bottom)
            ys.append(goldfish_hand(self.deck, hand, bottom, seed=seed * 1000 + i))
            rows.append(hand_features(hand, self.deck))
        cut = int(n * 0.8)
        self.model.fit(rows[:cut], ys[:cut], l2=2.0)
        self.r2 = self.model.r2(rows[cut:], ys[cut:])
        self.model.fit(rows, ys, l2=2.0)
        self.train_scores = sorted(ys)
        seven_scores = []
        for _ in range(2000):
            seven_scores.append(self.predict(self.rng.sample(self.deck.cards, 7)))
        self.random_seven = sorted(seven_scores)

    def predict(self, hand: list) -> float:
        return self.model.predict(hand_features(hand, self.deck))

    def best_keep(self, seven: list, n_bottom: int) -> tuple:
        """Which cards to put on the bottom: greedily remove the card whose loss hurts least."""
        hand, bottom = list(seven), []
        for _ in range(n_bottom):
            best = max(range(len(hand)), key=lambda i: self.predict(hand[:i] + hand[i + 1:]))
            bottom.append(hand.pop(best))
        return hand, bottom

    def _baseline(self, samples: int) -> None:
        """self.mull_value[m] = expected score if you take your m-th mulligan and then play well."""
        max_m = 1
        while 7 - bottom_count(max_m, self.free_first) > MIN_HAND:
            max_m += 1
        values = {}
        for m in range(max_m, 0, -1):
            nb = bottom_count(m, self.free_first)
            total = 0.0
            for _ in range(samples):
                seven = self.rng.sample(self.deck.cards, 7)
                keep, _ = self.best_keep(seven, nb)
                v = self.predict(keep)
                if m < max_m:
                    v = max(v, values[m + 1])
                total += v
            values[m] = total / samples
        self.mull_value = values
        self.max_mulligans = max_m

    # ---- advice -------------------------------------------------------------------------
    def percentile(self, score: float) -> float:
        below = sum(1 for s in self.random_seven if s < score)
        return 100 * below / len(self.random_seven)

    def evaluate(self, hand: list, mulligans_taken: int = 0, sims: int = 300, seed: int = 99) -> dict:
        """Should you keep this hand? ``hand`` is the 7 cards you drew; after mulligans, the
        advisor picks which ones to put on the bottom."""
        nb = bottom_count(mulligans_taken, self.free_first)
        keep, bottom = self.best_keep(hand, nb) if nb else (list(hand), [])
        sim_scores = [goldfish_hand(self.deck, keep, bottom, seed=seed * 7919 + i) for i in range(sims)]
        sim = sum(sim_scores) / sims
        model_score = self.predict(keep)
        next_m = mulligans_taken + 1
        mull = self.mull_value.get(next_m)
        if mull is None:
            decision, margin = "keep", None
        else:
            decision = "keep" if sim >= mull else "mulligan"
            margin = sim - mull
        return {
            "decision": decision,
            "keep": [c.name for c in keep], "bottom": [c.name for c in bottom],
            "expected_score": sim, "model_score": model_score, "mulligan_value": mull,
            "margin": margin, "percentile": self.percentile(model_score),
            "reasons": self.reasons(keep),
            "contributions": self.model.contributions(hand_features(keep, self.deck))[:5],
            "cards_after_mulligan": 7 - bottom_count(next_m, self.free_first) if mull is not None else None,
        }

    def policy(self, game, pl, hand: list, m: int):
        """Fast in-game mulligan decisions with the model (used by the matchup simulator)."""
        nb = bottom_count(m, self.free_first)
        keep, bottom = self.best_keep(hand, nb) if nb else (list(hand), [])
        nxt = self.mull_value.get(m + 1)
        if nxt is None or self.predict(keep) >= nxt:
            return keep, bottom
        return None

    def reasons(self, hand: list) -> list:
        lands = [c for c in hand if c.is_land]
        spells = [c for c in hand if not c.is_land]
        out = []
        nl = len(lands)
        if nl <= 1:
            out.append(f"Only {nl} land{'s' if nl != 1 else ''}: you'll likely miss early land drops.")
        elif nl >= 5:
            out.append(f"{nl} lands: likely to flood with few spells to cast.")
        else:
            out.append(f"{nl} lands: a workable start.")
        ramp = [c.name for c in spells if "ramp" in c.tags]
        if ramp:
            out.append("Ramp: " + ", ".join(ramp) + ".")
        colors = set()
        for c in lands + spells:
            for u in _sources(c):
                colors |= u
        missing = sorted(set(self.deck.colors) - colors)
        if missing:
            need = [c.name for c in spells if c.cost.colors & set(missing)]
            msg = "Missing color" + ("s" if len(missing) > 1 else "") + ": " + ", ".join(missing)
            if need:
                msg += f" (needed for {', '.join(need[:3])}{'...' if len(need) > 3 else ''})"
            out.append(msg + ".")
        cheap = [c.name for c in spells if c.cmc <= 2 and "ramp" not in c.tags]
        if cheap:
            out.append("Early plays: " + ", ".join(cheap) + ".")
        heavy = [c.name for c in spells if c.cmc >= 5]
        if heavy and nl <= 3:
            out.append("Expensive cards that will sit in hand early: " + ", ".join(heavy) + ".")
        fort = [c.name for c in spells if c.tags & {"attack_tax", "attack_restriction", "damage_prevention",
                                                     "attack_deterrent"}]
        if fort:
            out.append("Defense piece: " + ", ".join(fort) + ".")
        return out


def find_cards(deck: LoadedDeck, names: list) -> list:
    """Turn typed card names into Card objects from the deck (case-insensitive, partial ok)."""
    pool = list(deck.cards)
    out, missing = [], []
    for raw in names:
        n = raw.strip().lower()
        if not n:
            continue
        match = next((c for c in pool if c.name.lower() == n or c.name.split(" // ")[0].lower() == n), None)
        if match is None:
            match = next((c for c in pool if c.name.lower().startswith(n)), None)
        if match is None:
            match = next((c for c in pool if n in c.name.lower()), None)
        if match is None:
            missing.append(raw)
        else:
            pool.remove(match)
            out.append(match)
    if missing:
        cmdrs = [c.name for c in getattr(deck, "commanders", [])]
        hit = [m for m in missing if any(m.strip().lower() in c.lower() for c in cmdrs)]
        msg = "Not in the deck: " + ", ".join(missing)
        if hit:
            msg += " (your commander starts in the command zone, so it's never part of your opening hand)"
        raise ValueError(msg)
    return out
