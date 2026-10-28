"""Deck list parsing.

Reads the text you get from Moxfield, Archidekt, MTG Arena, MTGO or a hand-typed list:

    1 Sol Ring
    1x Swords to Plowshares (FRC) 37 *F*
    10 Plains (MSH) 434
    COMMANDER:
    1 Kros, Defense Contractor (NCC) 7 *F*

Set codes, collector numbers, foil markers and Archidekt [categories] are kept but not needed.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

_SECTIONS = {
    "commander": "commander", "commanders": "commander", "command zone": "commander",
    "deck": "main", "main": "main", "mainboard": "main", "main deck": "main", "library": "main",
    "creatures": "main", "lands": "main", "spells": "main",
    "sideboard": "sideboard", "companion": "sideboard",
    "maybeboard": "maybe", "considering": "maybe", "tokens": "maybe",
    "about": "about",
}

_QTY_RE = re.compile(r"^(\d+)\s*[xX]?\s+(.+)$")
_MARKER_RE = re.compile(r"\s+(\*[A-Za-z]+\*|\[[^\]]*\]|\^[^^]*\^)")
_SET_RE = re.compile(r"\s+\(([A-Za-z0-9]{2,6})\)(?:\s+([^\s]+))?\s*$")


@dataclass
class DeckEntry:
    name: str
    quantity: int = 1
    set_code: str | None = None
    collector_number: str | None = None
    foil: bool = False

    @property
    def lookup_name(self) -> str:
        """The name to search Scryfall for. Split and double-faced cards use the front face."""
        return re.split(r"\s+//?\s+", self.name)[0].strip()


@dataclass
class Deck:
    name: str = "Deck"
    commanders: list = field(default_factory=list)
    main: list = field(default_factory=list)
    sideboard: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    # Groups of 1-2 cards set apart by blank lines at the start or end of the list. Some exports
    # (MTGO style) put the commander there without a label; the loader checks them.
    edge_blocks: list = field(default_factory=list)

    @property
    def warnings(self) -> list:
        out = list(self.notes)
        if not self.commanders:
            out.append("No commander found. Add a 'COMMANDER:' line above it, or choose one.")
        elif self.total_count != 100:
            out.append(f"Deck has {self.total_count} cards including the commander (Commander decks have 100).")
        return out

    def to_text(self) -> str:
        """The deck as plain deck-list text (the same format this program reads)."""
        lines = [f"{e.quantity} {e.name}" for e in self.main]
        if self.sideboard:
            lines += ["", "SIDEBOARD:"] + [f"{e.quantity} {e.name}" for e in self.sideboard]
        if self.commanders:
            lines += ["", "COMMANDER:"] + [f"{e.quantity} {e.name}" for e in self.commanders]
        return "\n".join(lines) + "\n"

    @property
    def main_count(self) -> int:
        return sum(e.quantity for e in self.main)

    @property
    def total_count(self) -> int:
        return self.main_count + sum(e.quantity for e in self.commanders)

    def all_entries(self) -> list:
        return self.commanders + self.main

    def set_commander(self, name: str) -> None:
        """Move a card named in the main deck into the command zone."""
        key = name.strip().lower()
        for entry in list(self.main):
            if entry.lookup_name.lower() == key or entry.name.lower() == key:
                self.main.remove(entry)
                if entry.quantity > 1:
                    self.main.append(DeckEntry(entry.name, entry.quantity - 1, entry.set_code,
                                               entry.collector_number, entry.foil))
                self.commanders.append(DeckEntry(entry.name, 1, entry.set_code, entry.collector_number, entry.foil))
                return
        if not any(c.lookup_name.lower() == key for c in self.commanders):
            self.commanders.append(DeckEntry(name.strip()))


    def change_commanders(self, names: list) -> None:
        """Make ``names`` the commander(s), moving any current commander back into the deck."""
        for old in self.commanders:
            match = next((e for e in self.main if e.lookup_name.lower() == old.lookup_name.lower()), None)
            if match:
                match.quantity += old.quantity
            else:
                self.main.append(old)
        self.commanders = []
        for n in names:
            self.set_commander(n)
        self.notes = [n for n in self.notes if not n.startswith("The list didn't mark a commander")]


def _section_of(line: str) -> str | None:
    t = line.strip().lower()
    t = t.lstrip("/#").strip()
    t = re.sub(r"\s*\(\d+\)$", "", t).rstrip(":").strip()
    t = re.sub(r"\s+\d+$", "", t)  # "Commander 1"
    return _SECTIONS.get(t)


def parse_line(line: str) -> tuple[DeckEntry, bool] | None:
    """Parse one card line. Returns (entry, marked_as_commander) or None if it isn't a card."""
    text = line.strip()
    if not text:
        return None
    m = _QTY_RE.match(text)
    qty, rest = (int(m.group(1)), m.group(2)) if m else (1, text)
    markers = [mk.lower() for mk in _MARKER_RE.findall(" " + rest)]
    rest = _MARKER_RE.sub("", " " + rest).strip()
    is_cmdr = any("cmdr" in mk or "commander" in mk for mk in markers)
    foil = any(mk in ("*f*", "*e*", "*etched*", "*foil*") for mk in markers)
    set_code = number = None
    sm = _SET_RE.search(rest)
    if sm:
        set_code, number = sm.group(1).upper(), sm.group(2)
        rest = rest[: sm.start()].strip()
    name = re.sub(r"\s+", " ", rest).strip()
    if not name or qty < 1:
        return None
    return DeckEntry(name=name, quantity=qty, set_code=set_code, collector_number=number, foil=foil), is_cmdr


def parse_deck(text: str, name: str | None = None, commander: str | list | None = None) -> Deck:
    """Parse deck list text into a Deck. ``commander`` names the commander(s) when the list
    doesn't mark them."""
    deck = Deck(name=name or "Deck")
    section = "main"
    seen: dict = {}
    blocks: list = []  # (main-deck entries, came after a section header) per blank-line group
    current: list = []
    current_header = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            if current:
                blocks.append((current, current_header))
            current, current_header = [], False
            continue
        sec = _section_of(line)
        if sec:
            if current:
                blocks.append((current, current_header))
            current, current_header = [], True
            section = sec
            continue
        if section == "about":
            if line.lower().startswith("name "):
                deck.name = name or line[5:].strip()
            continue
        if line.startswith("#") or (line.startswith("//") and not _section_of(line)):
            continue
        parsed = parse_line(line)
        if not parsed:
            deck.notes.append(f"Couldn't read line: {line!r}")
            continue
        entry, is_cmdr = parsed
        target = "commander" if is_cmdr else section
        if target == "maybe":
            continue
        key = (target, entry.lookup_name.lower())
        if key in seen:
            seen[key].quantity += entry.quantity
            continue
        seen[key] = entry
        {"commander": deck.commanders, "main": deck.main, "sideboard": deck.sideboard}[target].append(entry)
        if target == "main":
            current.append(entry)
    if current:
        blocks.append((current, current_header))
    if len(blocks) >= 2:
        for entries, header in (blocks[-1], blocks[0]):
            if not header and 1 <= len(entries) <= 2 and sum(e.quantity for e in entries) <= 2:
                deck.edge_blocks.append(entries)
    if commander:
        for c in [commander] if isinstance(commander, str) else commander:
            deck.set_commander(c)
    return deck


def load_deck(path: str | Path, commander: str | list | None = None) -> Deck:
    path = Path(path)
    return parse_deck(path.read_text(encoding="utf-8"), name=path.stem, commander=commander)
