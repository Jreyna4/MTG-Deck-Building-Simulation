"""A simple black-and-white window for the simulator.

Start it with   python -m mtgsim   (or double-click start_gui.pyw on Windows).

It's built with tkinter, which comes with Python, so there's nothing extra to install. Every
option from the command line is here: import a deck (including Moxfield exports), deck roles,
card tags, opening hands, the mulligan advisor, combos and matchups with card defense scores.
Long simulations run in the background so the window stays responsive, and can be stopped.
"""
from __future__ import annotations

import os
import queue
import random
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox

from . import moxfield, report, settings
from .archetypes import ARCHETYPES, archetype
from .combos import ComboError
from .deck import Deck, parse_deck
from .loader import LoadedDeck, build_loaded_deck
from .matchup import Stopped
from .scryfall import CardCache, ScryfallError
from .tagger import DEFENSE_TAGS

BG, FG, DIM = "black", "white", "gray55"
DECKS_DIR = Path(__file__).resolve().parent.parent / "decks"
EXAMPLE_DECK = DECKS_DIR / "kros_pillow_fort_v5.txt"
MONO_FONTS = ("Cascadia Mono", "Consolas", "Menlo", "SF Mono", "DejaVu Sans Mono", "Liberation Mono",
              "Courier New")
FONT = BOLD = None  # set when the window opens

HELP = """MTG DECK BUILDING SIMULATION
============================

1. Import a deck                                   press I
   Moxfield: open the deck, choose More > Export, copy the plain text and paste it in.
   Archidekt, MTG Arena and MTGO text exports work too, or open a .txt file.
   R reopens the last deck you loaded. C changes the commander.

2. Pick an option on the left, or press its key
   1  Deck & roles      what the deck is made of, grouped by role
   2  Card tags         what the program thinks every card does, with a quick defense estimate
   3  Opening hands     land odds, role odds by turn, best and worst opening-hand cards
   4  Mulligan advisor  pick the 7 cards you drew and get keep or mulligan
   5  Combos            combos from Commander Spellbook (needs internet)
   6  Matchup           simulated games against practice decks or your friends' decks,
                        with a defense score for each card

   S saves what's shown here to a text file.   X stops a long simulation.   Q quits.

Card data comes from Scryfall the first time a deck is loaded and is saved, so it works offline
after that. The simulator is simplified: see the README's "Limits" section before trusting the
numbers.
"""


# =============================================================================================
# Small black-and-white widgets. Buttons are drawn from labels so they look the same on
# Windows, Mac and Linux (Mac ignores colors on normal buttons).
# =============================================================================================
def rule(parent, pady=(4, 8)):
    tk.Frame(parent, height=1, bg=FG).pack(fill="x", pady=pady)


def text_label(parent, text="", bold=False, dim=False, wrap=0, **kw):
    return tk.Label(parent, text=text, bg=BG, fg=DIM if dim else FG, font=BOLD if bold else FONT, anchor="w",
                    justify="left", wraplength=wrap, **kw)


def entry(parent, width=30, value=""):
    e = tk.Entry(parent, width=width, bg=BG, fg=FG, insertbackground=FG, relief="flat", font=FONT,
                 highlightthickness=1, highlightbackground=DIM, highlightcolor=FG, selectbackground=FG,
                 selectforeground=BG, disabledbackground=BG)
    if value:
        e.insert(0, str(value))
    return e


def _scrollbar(parent, orient, command):
    return tk.Scrollbar(parent, orient=orient, command=command, bg=BG, troughcolor=BG, activebackground=FG,
                        highlightthickness=0, relief="flat", bd=0)


def textbox(parent, height=10, width=60, wrap="none", xscroll=False):
    frame = tk.Frame(parent, bg=BG, highlightthickness=1, highlightbackground=DIM)
    text = tk.Text(frame, height=height, width=width, wrap=wrap, bg=BG, fg=FG, insertbackground=FG, relief="flat",
                   font=FONT, selectbackground=FG, selectforeground=BG, padx=8, pady=6, undo=True, bd=0,
                   highlightthickness=0)
    ys = _scrollbar(frame, "vertical", text.yview)
    text.configure(yscrollcommand=ys.set)
    ys.pack(side="right", fill="y")
    if xscroll:
        xs = _scrollbar(frame, "horizontal", text.xview)
        text.configure(xscrollcommand=xs.set)
        xs.pack(side="bottom", fill="x")
    text.pack(side="left", fill="both", expand=True)
    return frame, text


def listbox(parent, height=12, width=34, multiple=False):
    frame = tk.Frame(parent, bg=BG, highlightthickness=1, highlightbackground=DIM)
    lb = tk.Listbox(frame, height=height, width=width, bg=BG, fg=FG, selectbackground=FG, selectforeground=BG,
                    relief="flat", font=FONT, activestyle="none", highlightthickness=0, bd=0, exportselection=False,
                    selectmode="multiple" if multiple else "browse")
    ys = _scrollbar(frame, "vertical", lb.yview)
    lb.configure(yscrollcommand=ys.set)
    ys.pack(side="right", fill="y")
    lb.pack(side="left", fill="both", expand=True)
    return frame, lb


class TextButton(tk.Label):
    """A text button like ``[1] Deck & roles``. It turns white when the mouse is over it."""

    def __init__(self, parent, text, command, key=None):
        self.base = f"[{key}] {text}" if key else f"[ {text} ]"
        super().__init__(parent, text=self.base, bg=BG, fg=FG, font=FONT, anchor="w", padx=4, pady=1,
                         cursor="hand2", takefocus=1, highlightthickness=1, highlightbackground=BG,
                         highlightcolor=FG)
        self.command = command
        self.enabled = True
        self.bind("<Enter>", lambda e: self._paint(True))
        self.bind("<Leave>", lambda e: self._paint(False))
        self.bind("<ButtonRelease-1>", self._click)
        self.bind("<Return>", self._click)
        self.bind("<space>", self._click)

    def _paint(self, hot):
        if not self.enabled:
            self.configure(bg=BG, fg=DIM)
        else:
            self.configure(bg=FG if hot else BG, fg=BG if hot else FG)

    def _click(self, _event=None):
        if self.enabled:
            self._paint(False)
            self.command()
        return "break"

    def set_enabled(self, on: bool):
        self.enabled = on
        self.configure(cursor="hand2" if on else "")
        self._paint(False)


class Toggle(tk.Label):
    """A checkbox drawn as text: ``[x] label`` / ``[ ] label``."""

    def __init__(self, parent, text, value=False):
        super().__init__(parent, bg=BG, fg=FG, font=FONT, anchor="w", cursor="hand2", takefocus=1,
                         highlightthickness=1, highlightbackground=BG, highlightcolor=FG)
        self.label, self.value = text, value
        self.bind("<ButtonRelease-1>", self.flip)
        self.bind("<space>", self.flip)
        self._draw()

    def flip(self, _event=None):
        self.value = not self.value
        self._draw()

    def _draw(self):
        self.configure(text=("[x] " if self.value else "[ ] ") + self.label)


class Choice(tk.Frame):
    """Pick one of several options, drawn as text: ``(*) one   ( ) two``."""

    def __init__(self, parent, options, value):
        super().__init__(parent, bg=BG)
        self.value = value
        self.labels = {}
        for val, txt in options:
            lbl = tk.Label(self, bg=BG, fg=FG, font=FONT, cursor="hand2")
            lbl.bind("<ButtonRelease-1>", lambda e, v=val: self.set(v))
            lbl.pack(side="left", padx=(0, 14))
            self.labels[val] = (lbl, txt)
        self.set(value)

    def set(self, value):
        self.value = value
        for val, (lbl, txt) in self.labels.items():
            lbl.configure(text=("(*) " if val == value else "( ) ") + txt)


# =============================================================================================
# Dialogs
# =============================================================================================
class Dialog(tk.Toplevel):
    def __init__(self, app, title):
        super().__init__(app.root)
        self.app, self.result = app, None
        self.title(title)
        self.configure(bg=BG, padx=16, pady=12)
        self.transient(app.root)
        self.bind("<Escape>", lambda e: self.destroy())
        text_label(self, title.upper(), bold=True).pack(anchor="w")
        rule(self)
        self.body = tk.Frame(self, bg=BG)
        self.body.pack(fill="both", expand=True)
        self.message = text_label(self, "", dim=True, wrap=640)
        self.message.pack(anchor="w", fill="x", pady=(8, 0))
        self.buttons = tk.Frame(self, bg=BG)
        self.buttons.pack(fill="x", pady=(8, 0))

    def button(self, text, command):
        b = TextButton(self.buttons, text, command)
        b.pack(side="left", padx=(0, 10))
        return b

    def say(self, msg):
        self.message.configure(text=msg)

    def run(self):
        """Show the dialog and wait until it's closed. Returns ``self.result``."""
        self.update_idletasks()
        root = self.app.root
        x = root.winfo_rootx() + max(0, (root.winfo_width() - self.winfo_reqwidth()) // 2)
        y = root.winfo_rooty() + max(0, (root.winfo_height() - self.winfo_reqheight()) // 3)
        self.geometry(f"+{x}+{y}")
        try:
            self.wait_visibility()
            self.grab_set()
        except tk.TclError:
            pass
        self.focus_set()
        self.wait_window()
        return self.result


@dataclass
class DeckSource:
    """Where a deck comes from: pasted/loaded text, or a Moxfield link."""
    kind: str      # "text" or "moxfield"
    text: str
    name: str = ""
    commander: str = ""

    def resolve(self) -> Deck:
        if self.kind == "moxfield":
            deck = moxfield.fetch_deck(self.text)
            if self.name:
                deck.name = self.name
        else:
            deck = parse_deck(self.text, name=self.name or "My deck")
        if self.commander.strip():
            deck.change_commanders([c.strip() for c in self.commander.split(";") if c.strip()])
        return deck


class ImportDialog(Dialog):
    def __init__(self, app, for_opponent=False):
        super().__init__(app, "Import an opponent deck" if for_opponent else "Import a deck")
        b = self.body
        text_label(b, "Moxfield: open your deck, choose More > Export, copy the plain text and paste it below.",
                   wrap=640).pack(anchor="w")
        text_label(b, "Archidekt, MTG Arena and MTGO text exports work too. Ctrl+V pastes (Cmd+V on a Mac).",
                   dim=True, wrap=640).pack(anchor="w", pady=(0, 6))
        frame, self.box = textbox(b, height=14, width=78)
        frame.pack(fill="both", expand=True)
        row = tk.Frame(b, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        TextButton(row, "Paste from clipboard", self.paste).pack(side="left", padx=(0, 10))
        TextButton(row, "Open a .txt file...", self.open_file).pack(side="left", padx=(0, 10))
        if EXAMPLE_DECK.exists():
            TextButton(row, "Example deck (Kros)", self.example).pack(side="left")
        form = tk.Frame(b, bg=BG)
        form.pack(fill="x", pady=(10, 0))
        text_label(form, "Deck name:").grid(row=0, column=0, sticky="w", pady=2)
        self.name = entry(form, 40, "Opponent" if for_opponent else "My deck")
        self.name.grid(row=0, column=1, sticky="w", padx=8)
        text_label(form, "Commander:").grid(row=1, column=0, sticky="w", pady=2)
        self.cmdr = entry(form, 40)
        self.cmdr.grid(row=1, column=1, sticky="w", padx=8)
        text_label(form, "only needed if the list doesn't say which card it is", dim=True).grid(
            row=1, column=2, sticky="w")
        self.button("Import", self.ok)
        self.button("Moxfield settings", self.mox_settings)
        self.button("Cancel", self.destroy)
        self.bind("<Control-Return>", lambda e: self.ok())
        self.box.focus_set()

    def paste(self):
        try:
            text = self.clipboard_get()
        except tk.TclError:
            self.say("The clipboard is empty. Copy the deck list on Moxfield first.")
            return
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)

    def _set_name(self, name):
        if self.name.get().strip() in ("", "My deck", "Opponent"):
            self.name.delete(0, "end")
            self.name.insert(0, name)

    def open_file(self):
        path = filedialog.askopenfilename(
            parent=self, title="Open a deck list",
            initialdir=str(DECKS_DIR) if DECKS_DIR.exists() else None,
            filetypes=[("Deck lists", "*.txt *.dek *.dck"), ("All files", "*.*")])
        if not path:
            return
        try:
            text = Path(path).read_text(encoding="utf-8", errors="replace")
        except OSError as e:
            self.say(f"Couldn't open that file: {e}")
            return
        self.box.delete("1.0", "end")
        self.box.insert("1.0", text)
        self._set_name(Path(path).stem)

    def example(self):
        self.box.delete("1.0", "end")
        self.box.insert("1.0", EXAMPLE_DECK.read_text(encoding="utf-8"))
        self._set_name(EXAMPLE_DECK.stem)

    def mox_settings(self):
        value = AskText(self.app, "Moxfield settings",
                        "Moxfield only lets other programs download decks from a link with its permission. If "
                        "Moxfield support (support@moxfield.com) gave you an API user agent for this program, "
                        "paste it here. Leave it empty to turn link importing off.",
                        settings.get("moxfield_user_agent", "")).run()
        if value is not None:
            settings.put("moxfield_user_agent", value.strip())
            self.say("Saved." if value.strip() else "Link importing is off.")

    def ok(self):
        text = self.box.get("1.0", "end").strip()
        name, cmdr = self.name.get().strip(), self.cmdr.get().strip()
        if not text:
            self.say("Paste a deck list first. " + moxfield.EXPORT_HELP)
            return
        if moxfield.is_link(text):
            if not moxfield.user_agent():
                self.say(moxfield.NEEDS_PERMISSION)
                return
            if name in ("My deck", "Opponent"):
                name = ""
            self.result = DeckSource("moxfield", text, name, cmdr)
        else:
            parsed = parse_deck(text)
            if not parsed.main and not parsed.commanders:
                self.say("Couldn't find any cards in that text. Each line should look like '1 Sol Ring'.")
                return
            self.result = DeckSource("text", text, name, cmdr)
        self.destroy()


class AskText(Dialog):
    def __init__(self, app, title, prompt, value=""):
        super().__init__(app, title)
        text_label(self.body, prompt, wrap=560).pack(anchor="w", pady=(0, 8))
        self.field = entry(self.body, 60, value)
        self.field.pack(anchor="w", fill="x")
        self.button("Save", self.ok)
        self.button("Cancel", self.destroy)
        self.field.bind("<Return>", lambda e: self.ok())
        self.field.focus_set()

    def ok(self):
        self.result = self.field.get()
        self.destroy()


class CommanderDialog(Dialog):
    def __init__(self, app, deck: LoadedDeck):
        super().__init__(app, "Choose the commander")
        cands = deck.commander_candidates()
        current = {c.name for c in deck.commanders}
        names = sorted({c.name for c in cands} | current)
        if not names:
            names = sorted({c.name for c, _ in deck.unique() if not c.is_land})
            text_label(self.body, "No legendary creatures found, so here is every card:", wrap=520).pack(anchor="w")
        else:
            text_label(self.body, "Pick your commander (pick two for partners). These are the legendary "
                                  "creatures in the list:", wrap=520).pack(anchor="w")
        frame, self.lb = listbox(self.body, height=min(14, max(4, len(names))), width=44, multiple=True)
        frame.pack(fill="both", expand=True, pady=(6, 0))
        self.names = names
        for i, n in enumerate(names):
            self.lb.insert("end", n)
            if n in current:
                self.lb.selection_set(i)
        self.button("Use selected", self.ok)
        self.button("No commander", self.none)
        self.button("Cancel", self.destroy)

    def ok(self):
        picked = [self.names[i] for i in self.lb.curselection()]
        if not picked:
            self.say("Click a card to select it first.")
            return
        if len(picked) > 2:
            self.say("Pick one commander, or two partners.")
            return
        self.result = picked
        self.destroy()

    def none(self):
        self.result = []
        self.destroy()


class MulliganDialog(Dialog):
    def __init__(self, app, deck: LoadedDeck):
        super().__init__(app, "Mulligan advisor")
        self.deck = deck
        self.counts = {c.name: n for c, n in deck.unique()}
        self.all_names = sorted(self.counts)
        self.hand: list = []
        text_label(self.body, "Pick the 7 cards you drew: double-click a card to add it, double-click a card in "
                              "your hand to take it out.", wrap=640).pack(anchor="w")
        frow = tk.Frame(self.body, bg=BG)
        frow.pack(fill="x", pady=(6, 4))
        text_label(frow, "Find:").pack(side="left")
        self.filter = entry(frow, 30)
        self.filter.pack(side="left", padx=6)
        self.filter.bind("<KeyRelease>", lambda e: self.refill())
        self.filter.bind("<Return>", lambda e: self.add_first())
        cols = tk.Frame(self.body, bg=BG)
        cols.pack(fill="both", expand=True)
        left = tk.Frame(cols, bg=BG)
        left.pack(side="left", fill="both", expand=True)
        text_label(left, "Your deck").pack(anchor="w")
        f1, self.deck_lb = listbox(left, height=14, width=38)
        f1.pack(fill="both", expand=True)
        right = tk.Frame(cols, bg=BG)
        right.pack(side="left", fill="both", expand=True, padx=(14, 0))
        self.hand_title = text_label(right, "Your hand (0/7)")
        self.hand_title.pack(anchor="w")
        f2, self.hand_lb = listbox(right, height=14, width=38)
        f2.pack(fill="both", expand=True)
        self.deck_lb.bind("<Double-Button-1>", lambda e: self.add())
        self.deck_lb.bind("<Return>", lambda e: self.add())
        self.hand_lb.bind("<Double-Button-1>", lambda e: self.remove())
        self.hand_lb.bind("<Delete>", lambda e: self.remove())
        row = tk.Frame(self.body, bg=BG)
        row.pack(fill="x", pady=(6, 0))
        TextButton(row, "Random hand", self.random_hand).pack(side="left", padx=(0, 10))
        TextButton(row, "Clear hand", self.clear).pack(side="left")
        mrow = tk.Frame(self.body, bg=BG)
        mrow.pack(fill="x", pady=(10, 0))
        text_label(mrow, "Mulligans already taken:  ").pack(side="left")
        self.mulls = Choice(mrow, [(i, str(i)) for i in range(4)], 0)
        self.mulls.pack(side="left")
        self.button("Keep or mulligan?", self.ok)
        self.button("Cancel", self.destroy)
        self.refill()
        self.filter.focus_set()

    def refill(self):
        f = self.filter.get().strip().lower()
        self.shown = [n for n in self.all_names if f in n.lower()]
        self.deck_lb.delete(0, "end")
        for n in self.shown:
            left = self.counts[n] - self.hand.count(n)
            self.deck_lb.insert("end", f"{n}  x{left}" if self.counts[n] > 1 else n)
        self.hand_lb.delete(0, "end")
        for n in self.hand:
            self.hand_lb.insert("end", n)
        self.hand_title.configure(text=f"Your hand ({len(self.hand)}/7)")

    def _add_name(self, name):
        if len(self.hand) >= 7:
            self.say("Your hand already has 7 cards.")
            return
        if self.hand.count(name) >= self.counts[name]:
            self.say(f"The deck only has {self.counts[name]} {name}.")
            return
        self.hand.append(name)
        self.say("")
        self.refill()

    def add(self):
        sel = self.deck_lb.curselection()
        if sel:
            self._add_name(self.shown[sel[0]])

    def add_first(self):
        if self.shown:
            self._add_name(self.shown[0])
            self.filter.delete(0, "end")
            self.refill()

    def remove(self):
        sel = self.hand_lb.curselection()
        if sel:
            self.hand.pop(sel[0])
            self.refill()

    def random_hand(self):
        self.hand = [c.name for c in random.sample(self.deck.cards, 7)]
        self.refill()

    def clear(self):
        self.hand = []
        self.refill()

    def ok(self):
        if len(self.hand) != 7:
            self.say(f"Pick exactly 7 cards (you have {len(self.hand)}).")
            return
        self.result = (list(self.hand), self.mulls.value)
        self.destroy()


class MatchupDialog(Dialog):
    def __init__(self, app):
        super().__init__(app, "Matchup")
        b = self.body
        self.imported: list = []
        text_label(b, "Opponents (1 to 3 in total)", bold=True).pack(anchor="w")
        prow = tk.Frame(b, bg=BG)
        prow.pack(fill="x", pady=(2, 0))
        text_label(prow, "Practice decks:  ").pack(side="left")
        self.practice = {}
        for key in ARCHETYPES:
            t = Toggle(prow, key, True)
            t.pack(side="left", padx=(0, 12))
            self.practice[key] = t
        text_label(b, "Your friends' decks:").pack(anchor="w", pady=(8, 0))
        irow = tk.Frame(b, bg=BG)
        irow.pack(fill="x")
        f, self.opp_lb = listbox(irow, height=3, width=40)
        f.pack(side="left")
        bcol = tk.Frame(irow, bg=BG)
        bcol.pack(side="left", padx=10, anchor="n")
        TextButton(bcol, "Add deck...", self.add_deck).pack(anchor="w")
        TextButton(bcol, "Remove", self.remove_deck).pack(anchor="w")
        rule(b, pady=(10, 8))
        grid = tk.Frame(b, bg=BG)
        grid.pack(fill="x")
        text_label(grid, "Games:").grid(row=0, column=0, sticky="w", pady=2)
        self.games = entry(grid, 8, 300)
        self.games.grid(row=0, column=1, sticky="w", padx=8)
        text_label(grid, "more games = steadier numbers (300 games is about 10 seconds)", dim=True).grid(
            row=0, column=2, sticky="w")
        text_label(grid, "Measure cards:").grid(row=1, column=0, sticky="w", pady=2)
        self.measure = Choice(grid, [("none", "no"), ("defense", "defense cards"), ("all", "all cards")], "none")
        self.measure.grid(row=1, column=1, columnspan=2, sticky="w", padx=8)
        text_label(grid, "Games per card:").grid(row=2, column=0, sticky="w", pady=2)
        self.per_card = entry(grid, 8, 300)
        self.per_card.grid(row=2, column=1, sticky="w", padx=8)
        text_label(grid, "measuring cards replays every game once per card, so it takes minutes",
                   dim=True).grid(row=2, column=2, sticky="w")
        text_label(grid, "CPU cores:").grid(row=3, column=0, sticky="w", pady=2)
        self.cores = entry(grid, 8, max(1, min(8, (os.cpu_count() or 2) - 1)))
        self.cores.grid(row=3, column=1, sticky="w", padx=8)
        text_label(grid, "used when measuring cards", dim=True).grid(row=3, column=2, sticky="w")
        self.sample = Toggle(b, "Show the play-by-play of one game", False)
        self.sample.pack(anchor="w", pady=(8, 0))
        self.smart = Toggle(b, "Use the trained mulligan model for my deck (slower start)", False)
        self.smart.pack(anchor="w")
        self.combos = Toggle(b, "Let decks win with Commander Spellbook combos (needs internet)", False)
        self.combos.pack(anchor="w")
        self.button("Run", self.ok)
        self.button("Cancel", self.destroy)

    def add_deck(self):
        if len(self.imported) >= 3:
            self.say("That's already 3 decks.")
            return
        src = ImportDialog(self.app, for_opponent=True).run()
        try:
            self.grab_set()
        except tk.TclError:
            pass
        if src:
            if not src.name or src.name == "Opponent":
                src.name = f"Opponent {len(self.imported) + 1}"
            self.imported.append(src)
            self.opp_lb.insert("end", src.name)

    def remove_deck(self):
        sel = self.opp_lb.curselection()
        if sel:
            self.imported.pop(sel[0])
            self.opp_lb.delete(sel[0])

    @staticmethod
    def _number(widget, low, high, label):
        try:
            v = int(widget.get().strip())
        except ValueError:
            raise ValueError(f"{label} should be a whole number.")
        if not low <= v <= high:
            raise ValueError(f"{label} should be between {low} and {high}.")
        return v

    def ok(self):
        practice = [k for k, t in self.practice.items() if t.value]
        total = len(practice) + len(self.imported)
        if not 1 <= total <= 3:
            self.say(f"Pick 1 to 3 opponents in total (you picked {total}).")
            return
        try:
            games = self._number(self.games, 10, 20000, "Games")
            per_card = self._number(self.per_card, 10, 20000, "Games per card")
            cores = self._number(self.cores, 1, 64, "CPU cores")
        except ValueError as e:
            self.say(str(e))
            return
        self.result = {"practice": practice, "imported": list(self.imported), "games": games,
                       "measure": self.measure.value, "per_card": per_card, "cores": cores,
                       "sample": self.sample.value, "smart": self.smart.value, "combos": self.combos.value}
        self.destroy()


# =============================================================================================
# Main window
# =============================================================================================
class App:
    def __init__(self, root):
        global FONT, BOLD
        self.root = root
        self.deck: LoadedDeck | None = None
        self.busy = False
        self.stop_event = threading.Event()
        self.q: queue.Queue = queue.Queue()
        self._advisor = (None, None)
        family = next((f for f in MONO_FONTS if f in set(tkfont.families(root))), "TkFixedFont")
        size = 12 if sys.platform == "darwin" else 10
        FONT = tkfont.Font(root=root, family=family, size=size)
        BOLD = tkfont.Font(root=root, family=family, size=size, weight="bold")
        root.title("MTG Deck Building Simulation")
        root.configure(bg=BG)
        root.geometry("1200x760")
        root.minsize(900, 560)
        self._build()
        self.show(HELP)
        self._refresh_header()
        root.bind("<KeyPress>", self._on_key)
        root.protocol("WM_DELETE_WINDOW", self.quit)
        root.after(100, self._poll)

    # ---- layout -------------------------------------------------------------------------
    def _build(self):
        top = tk.Frame(self.root, bg=BG, padx=12, pady=8)
        top.pack(fill="x")
        text_label(top, "MTG DECK BUILDING SIMULATION", bold=True).pack(anchor="w")
        self.header = text_label(top, "")
        self.header.pack(anchor="w")
        tk.Frame(self.root, height=1, bg=FG).pack(fill="x")

        body = tk.Frame(self.root, bg=BG)
        body.pack(fill="both", expand=True)
        menu = tk.Frame(body, bg=BG, padx=10, pady=8)
        menu.pack(side="left", fill="y")
        tk.Frame(body, width=1, bg=FG).pack(side="left", fill="y")
        out = tk.Frame(body, bg=BG, padx=8, pady=8)
        out.pack(side="left", fill="both", expand=True)

        self.buttons = {}

        def section(title):
            text_label(menu, title, dim=True).pack(anchor="w", pady=(10, 2))

        def add(key, text, command):
            b = TextButton(menu, text, command, key=key.upper())
            b.pack(anchor="w", fill="x")
            self.buttons[key] = b

        section("DECK")
        add("i", "Import deck", self.import_deck)
        add("r", "Reopen last deck", self.reopen_last)
        add("c", "Choose commander", self.choose_commander)
        section("ANALYZE")
        add("1", "Deck & roles", self.show_roles)
        add("2", "Card tags", self.show_tags)
        add("3", "Opening hands", self.opening_hands)
        add("4", "Mulligan advisor", self.mulligan)
        add("5", "Combos", self.find_combos)
        add("6", "Matchup", self.matchup)
        section("OTHER")
        add("s", "Save output", self.save_output)
        add("h", "Help", lambda: self.show(HELP))
        add("q", "Quit", self.quit)

        frame, self.out = textbox(out, height=30, width=100, xscroll=True)
        frame.configure(highlightthickness=0)
        frame.pack(fill="both", expand=True)
        self.out.configure(state="disabled", cursor="arrow")

        tk.Frame(self.root, height=1, bg=FG).pack(fill="x")
        bottom = tk.Frame(self.root, bg=BG, padx=12, pady=4)
        bottom.pack(fill="x")
        self.status = text_label(bottom, "Ready.")
        self.status.pack(side="left", fill="x", expand=True)
        self.stop_btn = TextButton(bottom, "Stop", self.stop, key="X")
        self.stop_btn.pack(side="right")
        self._set_busy(False)

    def _refresh_header(self):
        d = self.deck
        if d is None:
            last = settings.last_deck()
            hint = f", or R to reopen {last['name']}" if last else ""
            self.header.configure(text=f"No deck loaded. Press I to import one{hint}.")
            return
        cmdr = " + ".join(c.name for c in d.commanders) or "none (press C to choose)"
        info = (f"Deck: {d.name}   |   Commander: {cmdr}   |   Colors: {''.join(sorted(d.colors))}   |   "
                f"{len(d.cards) + len(d.commanders)} cards, {d.land_count} lands")
        if d.not_found:
            info += f"   |   {len(d.not_found)} not found"
        self.header.configure(text=info)

    def _set_busy(self, busy: bool):
        self.busy = busy
        for key, b in self.buttons.items():
            if key in ("h", "q", "s"):
                continue
            on = not busy
            if key == "r":
                on = on and settings.last_deck() is not None
            b.set_enabled(on)
        self.stop_btn.set_enabled(busy)

    # ---- output and status --------------------------------------------------------------
    def show(self, text: str):
        self.out.configure(state="normal")
        self.out.delete("1.0", "end")
        self.out.insert("1.0", text.lstrip("\n"))
        self.out.configure(state="disabled")
        self.out.yview_moveto(0)
        self.out.xview_moveto(0)

    def output_text(self) -> str:
        return self.out.get("1.0", "end").rstrip() + "\n"

    def set_status(self, msg: str):
        self.status.configure(text=msg)

    def post(self, msg: str):
        """Update the status line from a background thread."""
        self.q.put(("status", msg, None))

    # ---- background jobs ----------------------------------------------------------------
    def run(self, title: str, work, done):
        """Run ``work()`` on a background thread, then ``done(result)`` back in the window."""
        if self.busy:
            return
        self.stop_event.clear()
        self._set_busy(True)
        self.set_status(title + "...")

        def target():
            try:
                result = work()
            except Stopped:
                self.q.put(("stopped", None, None))
            except Exception as e:  # shown to the user in the window
                self.q.put(("error", e, traceback.format_exc()))
            else:
                self.q.put(("done", done, result))

        threading.Thread(target=target, daemon=True).start()

    def _poll(self):
        try:
            while True:
                kind, a, b = self.q.get_nowait()
                if kind == "status":
                    self.set_status(a)
                    continue
                self._set_busy(False)
                if kind == "stopped" or (kind == "done" and self.stop_event.is_set()):
                    self.set_status("Stopped.")
                elif kind == "error":
                    self._show_error(a, b)
                else:
                    self.set_status("Done.")
                    a(b)
        except queue.Empty:
            pass
        self.root.after(100, self._poll)

    def _show_error(self, exc, tb):
        known = (ScryfallError, ComboError, moxfield.MoxfieldError, ValueError, OSError)
        if isinstance(exc, known):
            self.set_status("Couldn't finish: " + str(exc)[:120])
            self.show("Couldn't finish.\n\n" + str(exc) + "\n")
        else:
            self.set_status("Something went wrong (details below).")
            self.show("Something went wrong. If it keeps happening, copy this into an issue on GitHub.\n\n" + tb)

    def stop(self):
        if self.busy:
            self.stop_event.set()
            self.set_status("Stopping...")

    def _need_deck(self) -> bool:
        if self.deck is None:
            self.set_status("Import a deck first.")
            self.import_deck()
            return False
        return True

    # ---- keyboard -----------------------------------------------------------------------
    def _on_key(self, event):
        if event.widget.winfo_toplevel() is not self.root or isinstance(event.widget, tk.Entry):
            return
        key = (event.char or "").lower()
        if key == "x":
            self.stop()
        elif key in self.buttons and self.buttons[key].enabled:
            self.buttons[key].command()

    # ---- deck ---------------------------------------------------------------------------
    def import_deck(self):
        src = ImportDialog(self).run()
        if src:
            self.load(src)

    def reopen_last(self):
        last = settings.last_deck()
        if last:
            self.load(DeckSource("text", last["text"], last.get("name") or "My deck"))

    def load(self, src: DeckSource):
        def work():
            deck = src.resolve()
            self.post("Looking up cards...")
            return build_loaded_deck(deck, cache=CardCache(), progress=self.post)
        self.run("Loading " + (src.name or "deck"), work, self._deck_loaded)

    def _deck_loaded(self, loaded: LoadedDeck):
        self.deck = loaded
        self._advisor = (None, None)
        settings.save_last_deck(loaded.name, loaded.deck.to_text())
        self._set_busy(False)
        self._refresh_header()
        self.show_roles()
        self.set_status(f"Loaded {loaded.name}. Pick an option on the left.")
        if not loaded.commanders:
            self.root.after(50, self.choose_commander)

    def choose_commander(self):
        if not self._need_deck():
            return
        names = CommanderDialog(self, self.deck).run()
        if names is None:
            return
        deck = self.deck.deck
        deck.change_commanders(names)
        self.run("Updating commander", lambda: build_loaded_deck(deck, cache=CardCache(), progress=self.post),
                 self._deck_loaded)

    # ---- options ------------------------------------------------------------------------
    def show_roles(self):
        if self._need_deck():
            self.show(report.deck_overview(self.deck))

    def show_tags(self):
        if self._need_deck():
            self.show(report.tag_table(self.deck) + "\n" + report.heading("Tag meanings") + "\n" +
                      report.tag_legend())

    def opening_hands(self):
        if not self._need_deck():
            return
        deck = self.deck

        def work():
            from .goldfish import run_goldfish
            from .handstats import opening_hand_stats
            self.post("Working out opening-hand odds...")
            stats = opening_hand_stats(deck)
            self.post("Playing 3000 goldfish games...")
            return report.hand_report(stats, run_goldfish(deck, games=3000))
        self.run("Opening hands", work, self.show)

    def mulligan(self):
        if not self._need_deck():
            return
        deck = self.deck
        picked = MulliganDialog(self, deck).run()
        if not picked:
            return
        names, mulls = picked
        from .mulligan import MulliganAdvisor, find_cards
        hand = find_cards(deck, names)

        def work():
            owner, adv = self._advisor
            if owner is not deck:
                self.post("Training the mulligan model on this deck (about 10 seconds, only the first time)...")
                adv = MulliganAdvisor(deck)
                self._advisor = (deck, adv)
            self.post("Playing this hand 400 times...")
            r = adv.evaluate(hand, mulligans_taken=mulls, sims=400)
            return report.mulligan_report(r) + (
                f"\n\n(Model fit on held-out hands: R² = {adv.r2:.2f}. The advice is based on 400 simulated "
                f"games with this exact hand.)")
        self.run("Mulligan advisor", work, self.show)

    def find_combos(self):
        if not self._need_deck():
            return
        deck = self.deck

        def work():
            from .combos import combos_for_deck
            self.post("Asking Commander Spellbook...")
            return report.combos_report(combos_for_deck(deck))
        self.run("Combos", work, self.show)

    def matchup(self):
        if not self._need_deck():
            return
        deck = self.deck
        opts = MatchupDialog(self).run()
        if not opts:
            return
        stop = self.stop_event.is_set

        def work():
            from .engine import Game
            from .matchup import card_impact, run_matchup
            opps = [archetype(k) for k in opts["practice"]]
            for src in opts["imported"]:
                self.post(f"Loading {src.name}...")
                opps.append(build_loaded_deck(src.resolve(), cache=CardCache(), progress=self.post))
            combos = None
            if opts["combos"]:
                from .combos import combos_for_deck, winning_combos
                try:
                    self.post("Asking Commander Spellbook for combos...")
                    combos = winning_combos(combos_for_deck(deck))
                    for o in opps:
                        if isinstance(o, LoadedDeck):
                            combos += winning_combos(combos_for_deck(o))
                except ComboError as e:
                    self.post(f"Skipping combos: {e}")
            mull = None
            if opts["smart"]:
                from .mulligan import MulliganAdvisor
                owner, adv = self._advisor
                if owner is not deck:
                    self.post("Training the mulligan model on your deck...")
                    adv = MulliganAdvisor(deck)
                    self._advisor = (deck, adv)
                mull = adv.policy
            parts = []
            if opts["sample"]:
                decks = [(deck.name, deck.cards, deck.commanders, deck.colors)] + \
                    [(o.name, o.cards, o.commanders, o.colors) for o in opps]
                g = Game(decks, seed=7, verbose=True, combos=combos, policies={0: mull} if mull else None)
                g.run()
                winner = g.players[g.winner].name if g.winner is not None else "nobody (time limit)"
                parts.append(report.heading("Sample game") + "\n" + "\n".join(g.events) + f"\nWinner: {winner}\n")
            self.post(f"Playing {opts['games']} games...")
            r = run_matchup(deck, opps, games=opts["games"], combos=combos, your_mulligan=mull, should_stop=stop)
            parts.insert(0, report.matchup_report(r))
            if opts["measure"] != "none":
                if opts["measure"] == "all":
                    cards = None
                else:
                    want = DEFENSE_TAGS | {"goad", "counters_on_opponents", "proliferate", "copy_enchantment"}
                    cards = [c.name for c, _ in deck.unique() if not c.is_land and c.tags & want]
                imp = card_impact(deck, opps, cards=cards, games=opts["per_card"], combos=combos,
                                  your_mulligan=mull, workers=opts["cores"], should_stop=stop,
                                  progress=lambda i, n, name: self.post(f"Measuring cards: {i}/{n} done ({name})"))
                parts.insert(1, report.impact_report(imp))
            return "\n\n".join(parts)
        self.run("Matchup", work, self.show)

    # ---- other --------------------------------------------------------------------------
    def save_output(self):
        name = (self.deck.name if self.deck else "mtgsim") + "_results.txt"
        path = filedialog.asksaveasfilename(parent=self.root, title="Save output", initialfile=name,
                                            defaultextension=".txt", filetypes=[("Text", "*.txt")])
        if path:
            Path(path).write_text(self.output_text(), encoding="utf-8")
            self.set_status(f"Saved to {path}")

    def quit(self):
        if self.busy:
            if not messagebox.askyesno("Quit", "A simulation is still running. Quit anyway?", parent=self.root):
                return
            os._exit(0)  # also ends any worker processes still measuring cards
        self.root.destroy()


def main() -> None:
    if sys.platform == "win32":
        try:  # sharp text on high-resolution screens
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    try:
        root = tk.Tk()
    except tk.TclError as e:
        print(f"Couldn't open a window ({e}). The command line still works: python -m mtgsim --help",
              file=sys.stderr)
        raise SystemExit(1)
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
