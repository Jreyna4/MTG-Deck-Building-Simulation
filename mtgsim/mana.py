"""Mana costs and paying for spells.

A mana *source* is a frozenset of the colors one unit of mana can be ("W", "U", "B", "R", "G"
or "C" for colorless). A Sol Ring is two sources of {"C"}; a Talisman of Curiosity is one
source of {"C", "G", "U"}.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

COLORS = "WUBRG"
_SYMBOL_RE = re.compile(r"\{([^}]*)\}")
ANY = frozenset(COLORS)


@dataclass(frozen=True)
class ManaCost:
    generic: int = 0
    pips: tuple = ()  # one frozenset per colored symbol: the colors that can pay it
    x_count: int = 0

    def value(self, x: int = 0) -> int:
        """Total mana needed, with X set to ``x``."""
        return self.generic + len(self.pips) + self.x_count * x

    @property
    def colors(self) -> set:
        out = set()
        for pip in self.pips:
            out |= pip
        return out - {"C"}


@lru_cache(maxsize=4096)
def parse_mana_cost(text: str | None) -> ManaCost:
    """Parse '{2}{G}{W/U}{X}' style costs. Hybrid symbols accept either color; Phyrexian
    symbols are treated as their color; snow counts as generic."""
    if not text:
        return ManaCost()
    text = text.split(" // ")[0]
    generic, x_count, pips = 0, 0, []
    for raw in _SYMBOL_RE.findall(text):
        sym = raw.upper()
        if sym.isdigit():
            generic += int(sym)
        elif sym == "X":
            x_count += 1
        elif sym in ("S",):
            generic += 1
        elif sym == "C":
            pips.append(frozenset("C"))
        elif sym in COLORS:
            pips.append(frozenset(sym))
        else:
            parts = [p for p in re.split(r"/", sym) if p]
            colors = frozenset(p for p in parts if p in COLORS)
            if not colors and len(sym) == 2 and all(ch in COLORS for ch in sym):
                colors = frozenset(sym)  # "GW" style hybrid
            if colors:
                pips.append(colors)
            elif parts and parts[0].isdigit():
                generic += int(parts[0])
    return ManaCost(generic=generic, pips=tuple(pips), x_count=x_count)


def _match_pips(pips, sources) -> list | None:
    """Give every colored pip its own source that can pay for it (bipartite matching).
    Tries the least flexible sources first so flexible ones stay free."""
    order = sorted(range(len(sources)), key=lambda i: len(sources[i]))
    owner = {}  # source index -> pip index

    def assign(p, seen):
        for s in order:
            if s in seen or not (pips[p] & sources[s]):
                continue
            seen.add(s)
            if s not in owner or assign(owner[s], seen):
                owner[s] = p
                return True
        return False

    # hardest pips (fewest acceptable colors) first
    for p in sorted(range(len(pips)), key=lambda i: len(pips[i])):
        if not assign(p, set()):
            return None
    return list(owner.keys())


def choose_payment(cost: ManaCost, sources: list, x: int = 0, extra: int = 0) -> list | None:
    """Return the indices of ``sources`` to tap to pay ``cost`` (plus ``extra`` generic mana),
    or None if it can't be paid. Keeps the most flexible sources untapped when it can."""
    total = cost.value(x) + extra
    if total > len(sources):
        return None
    used = _match_pips(cost.pips, sources)
    if used is None:
        return None
    used_set = set(used)
    rest = [i for i in range(len(sources)) if i not in used_set]
    rest.sort(key=lambda i: (len(sources[i]), "C" not in sources[i]))
    need = total - len(cost.pips)
    return used + rest[:need]


def can_pay(cost: ManaCost, sources: list, x: int = 0, extra: int = 0) -> bool:
    return choose_payment(cost, sources, x, extra) is not None


_ADD_RE = re.compile(r"add ([^.]*?)(?:\.|$)", re.I)
_BASIC_COLORS = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G", "wastes": "C"}


def sources_from_text(add_phrase: str, deck_colors: frozenset) -> list:
    """Turn the words after 'Add' into mana sources, e.g. '{C}{C}' -> two {C} sources,
    '{G} or {U}' -> one {G,U} source, 'one mana of any color' -> one source of the deck's colors."""
    phrase = add_phrase.lower()
    any_colors = deck_colors or ANY
    if "any color" in phrase or "any one color" in phrase or "any type" in phrase:
        n = 2 if phrase.startswith("two mana") else 3 if phrase.startswith("three mana") else 1
        return [frozenset(any_colors)] * n
    symbols = _SYMBOL_RE.findall(add_phrase.upper())
    if not symbols:
        return []
    if " or " in phrase:
        # "{G} or {U}" or "{W}, {U}, or {B}" -> one unit of any of those
        return [frozenset(s for s in symbols if s in COLORS or s == "C")]
    return [frozenset(s) for s in symbols if s in COLORS or s == "C"]


def land_sources(type_line: str, text: str, produced: list | None, deck_colors: frozenset) -> list:
    """Mana a land makes when tapped (one unit, possibly several color options)."""
    colors = set()
    if produced:
        colors |= set(produced)
    tl = type_line.lower()
    for word, c in _BASIC_COLORS.items():
        if re.search(rf"\b{word}\b", tl):
            colors.add(c)
    low = text.lower()
    if not colors:
        for m in _ADD_RE.finditer(low):
            for src in sources_from_text(m.group(1), deck_colors):
                colors |= src
    if not colors and "search your library" in low:  # fetch lands: count as any deck color
        colors |= set(deck_colors)
    if "any color" in low or "any one color" in low:
        colors |= set(deck_colors)
    # A land only matters for the colors this deck actually uses
    usable = {c for c in colors if c == "C" or c in deck_colors}
    if colors and not usable:
        usable = {"C"}
    if not usable:
        return []
    amount = 1
    if re.search(r"add \{c\}\{c\}", low):
        amount = 2
    return [frozenset(usable)] * amount
