"""Opening-hand statistics.

Exact probabilities (hypergeometric math) for what you'll see in your opening 7 and by each turn,
plus a quick Monte Carlo for things that depend on several card types at once.
"""
from __future__ import annotations

import random
from collections import Counter
from math import comb

from .loader import LoadedDeck
from .tagger import ROLE_GROUPS


def p_at_least(copies: int, deck_size: int, seen: int, at_least: int = 1) -> float:
    """Chance of seeing at least ``at_least`` of ``copies`` cards when you've seen ``seen`` of
    ``deck_size`` cards."""
    seen = min(seen, deck_size)
    total = comb(deck_size, seen)
    miss = sum(comb(copies, i) * comb(deck_size - copies, seen - i) for i in range(0, min(at_least, copies + 1)))
    return 1 - miss / total


def cards_seen_by_turn(turn: int, on_play: bool) -> int:
    return 7 + turn - (1 if on_play else 0)


def opening_hand_stats(deck: LoadedDeck, samples: int = 20000, seed: int = 1, max_turn: int = 6) -> dict:
    cards = deck.cards
    n = len(cards)
    lands = sum(1 for c in cards if c.is_land)
    rng = random.Random(seed)
    land_hist = Counter()
    flags = Counter()
    for _ in range(samples):
        hand = rng.sample(cards, 7)
        nl = sum(1 for c in hand if c.is_land)
        land_hist[nl] += 1
        spells = [c for c in hand if not c.is_land]
        has_ramp = any("ramp" in c.tags for c in spells)
        cheap = any(c.cmc <= 2 for c in spells)
        colors = set()
        for c in hand:
            for u in c.params.get("mana", []):
                colors |= u
        flags["2-4 lands"] += 2 <= nl <= 4
        flags["3-4 lands"] += 3 <= nl <= 4
        flags["0-1 lands"] += nl <= 1
        flags["5+ lands"] += nl >= 5
        flags["ramp"] += has_ramp
        flags["cheap play (2 mana or less)"] += cheap
        flags["card draw"] += any("draw" in c.tags or "land_tutor" in c.tags for c in spells)
        flags["pillow fort piece"] += any(c.tags & {"attack_tax", "attack_restriction", "damage_prevention",
                                                    "attack_deterrent", "fog"} for c in spells)
        flags["all commander colors from lands"] += set(deck.colors) <= colors
        flags["2-4 lands + ramp or cheap play"] += 2 <= nl <= 4 and (has_ramp or cheap)
    by_role = []
    for title, tagset in ROLE_GROUPS:
        k = sum(1 for c in cards if c.tags & tagset)
        if not k:
            continue
        row = {"role": title, "count": k}
        for t in (1, 2, 3, 4, 6):
            row[f"turn_{t}"] = p_at_least(k, n, cards_seen_by_turn(t, on_play=False))
        row["opening_7"] = p_at_least(k, n, 7)
        by_role.append(row)
    land_drops = []
    for t in range(1, max_turn + 1):
        land_drops.append({
            "turn": t,
            "on_play": p_at_least(lands, n, cards_seen_by_turn(t, True), at_least=t),
            "on_draw": p_at_least(lands, n, cards_seen_by_turn(t, False), at_least=t),
        })
    single = [{"turn": t, "chance": min(1.0, cards_seen_by_turn(t, False) / n)} for t in (0, 1, 2, 3, 4, 6, 8)]
    single[0]["turn"] = "opening hand"
    single[0]["chance"] = 7 / n
    return {
        "deck_size": n, "lands": lands,
        "nonland": n - lands,
        "avg_lands_in_7": sum(k * v for k, v in land_hist.items()) / samples,
        "land_distribution": {k: land_hist[k] / samples for k in range(8)},
        "hand_flags": {k: v / samples for k, v in flags.items()},
        "by_role": by_role,
        "land_drops": land_drops,
        "single_card": single,
        "avg_mana_value": sum(c.cmc for c in cards if not c.is_land) / max(1, n - lands),
        "curve": dict(sorted(Counter(min(int(c.cmc), 7) for c in cards if not c.is_land).items())),
    }
