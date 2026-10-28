"""Command-line interface.

    python -m mtgsim                      (no command: opens the window)
    python -m mtgsim analyze decks/my_deck.txt
    python -m mtgsim mulligan decks/my_deck.txt --hand "Forest; Island; Sol Ring; ..."
    python -m mtgsim combos decks/my_deck.txt
    python -m mtgsim matchup decks/my_deck.txt --vs tokens stompy voltron --impact defense
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import report
from .archetypes import ARCHETYPES, archetype
from .loader import load
from .tagger import DEFENSE_TAGS


def _load(args, path=None):
    def progress(msg):
        print(msg, file=sys.stderr)
    deck = load(path or args.deck, commander=getattr(args, "commander", None), cache_path=args.cache,
                offline=args.offline, overrides_path=args.overrides, progress=progress)
    if deck.not_found:
        print("Warning: not found on Scryfall: " + ", ".join(deck.not_found), file=sys.stderr)
    return deck


def _opponents(args):
    opps = []
    for v in args.vs:
        if v.lower() in ARCHETYPES:
            opps.append(archetype(v))
        elif Path(v).exists():
            opps.append(_load(args, v))
        else:
            raise SystemExit(f"--vs {v!r} is neither a practice opponent ({', '.join(ARCHETYPES)}) nor a deck file")
    if not 1 <= len(opps) <= 3:
        raise SystemExit("Give one to three opponents with --vs")
    return opps


def _dump(args, data):
    if getattr(args, "json", None):
        Path(args.json).write_text(json.dumps(data, indent=1, default=_jsonable), encoding="utf-8")
        print(f"\nSaved results to {args.json}")


def _jsonable(o):
    if isinstance(o, (set, frozenset)):
        return sorted(o)
    return str(o)


def cmd_analyze(args):
    from .goldfish import run_goldfish
    from .handstats import opening_hand_stats
    deck = _load(args)
    print(report.deck_overview(deck))
    print(report.tag_table(deck))
    stats = opening_hand_stats(deck)
    gold = run_goldfish(deck, games=args.games)
    print(report.hand_report(stats, gold))
    _dump(args, {"hand_stats": stats, "goldfish": gold})


def cmd_tags(args):
    deck = _load(args)
    print(report.deck_overview(deck))
    print(report.tag_table(deck))
    if args.legend:
        print(report.heading("Tag meanings"))
        print(report.tag_legend())


def cmd_mulligan(args):
    from .mulligan import MulliganAdvisor, find_cards
    deck = _load(args)
    names = [n for n in args.hand.replace("\n", ";").split(";") if n.strip()]
    if len(names) != 7:
        raise SystemExit(f"Give exactly 7 cards separated by ';' (got {len(names)}).")
    try:
        hand = find_cards(deck, names)
    except ValueError as e:
        raise SystemExit(str(e))
    print("Training the mulligan model on this deck (takes a few seconds)...", file=sys.stderr)
    adv = MulliganAdvisor(deck, train_hands=args.train)
    r = adv.evaluate(hand, mulligans_taken=args.mulligans, sims=args.sims)
    print(report.mulligan_report(r))
    print(f"\n(Model fit on held-out hands: R² = {adv.r2:.2f}. Goldfish games are noisy, so the advice is based "
          f"on {args.sims} simulated games with this exact hand.)")
    _dump(args, r)


def cmd_combos(args):
    from .combos import ComboError, combos_for_deck
    deck = _load(args)
    try:
        res = combos_for_deck(deck, use_cache=not args.refresh)
    except ComboError as e:
        raise SystemExit(str(e))
    print(report.combos_report(res, limit=args.limit))
    _dump(args, res)


def cmd_matchup(args):
    from .matchup import card_impact, run_matchup
    deck = _load(args)
    opps = _opponents(args)
    combos = None
    if args.combos:
        from .combos import ComboError, combos_for_deck, winning_combos
        try:
            combos = winning_combos(combos_for_deck(deck))
            for o in opps:
                if not getattr(o, "commanders", [None])[0] or getattr(o.commanders[0], "synthetic", False):
                    continue
                combos += winning_combos(combos_for_deck(o))
        except ComboError as e:
            print(f"Skipping combos: {e}", file=sys.stderr)
    mull = None
    if args.smart_mulligans:
        from .mulligan import MulliganAdvisor
        print("Training mulligan model...", file=sys.stderr)
        mull = MulliganAdvisor(deck).policy
    if args.log:
        from .engine import Game
        decks = [(deck.name, deck.cards, deck.commanders, deck.colors)] + \
            [(o.name, o.cards, o.commanders, o.colors) for o in opps]
        g = Game(decks, seed=args.log, verbose=True, max_rounds=args.rounds, combos=combos,
                 policies={0: mull} if mull else None)
        g.run()
        print(report.heading(f"Sample game (seed {args.log})"))
        print("\n".join(g.events))
        winner = g.players[g.winner].name if g.winner is not None else "nobody (time limit)"
        print(f"Winner: {winner}")
    r = run_matchup(deck, opps, games=args.games, max_rounds=args.rounds, combos=combos, your_mulligan=mull)
    print(report.matchup_report(r))
    out = {"matchup": r}
    if args.impact != "none":
        if args.impact == "all":
            cards = None
        elif args.impact == "defense":
            want = DEFENSE_TAGS | {"goad", "counters_on_opponents", "proliferate", "copy_enchantment"}
            cards = [c.name for c, _ in deck.unique() if not c.is_land and c.tags & want]
        else:
            cards = [n.strip() for n in args.impact.split(";") if n.strip()]

        def progress(i, n, name):
            print(f"  [{i}/{n}] {name}", file=sys.stderr)
        imp = card_impact(deck, opps, cards=cards, games=args.impact_games, max_rounds=args.rounds,
                          combos=combos, progress=progress, your_mulligan=mull, workers=args.workers)
        print(report.impact_report(imp))
        out["impact"] = imp
    _dump(args, out)


def cmd_gui(args):
    try:
        import tkinter  # noqa: F401
    except ImportError:
        raise SystemExit("The window needs tkinter, which this Python doesn't have.\n"
                         "  Windows/Mac: install Python from python.org (tkinter is included).\n"
                         "  Linux: install the python3-tk package (for example: sudo apt install python3-tk).\n"
                         "The command line still works: python -m mtgsim --help")
    from .gui import main as gui_main
    gui_main()


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return cmd_gui(None)
    p = argparse.ArgumentParser(prog="mtgsim", description="Commander deck simulator. "
                                "Run it with no command to open the window.")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("gui", help="open the window").set_defaults(func=cmd_gui)

    def common(sp):
        sp.add_argument("deck", help="deck list file (Moxfield/Archidekt/Arena text export)")
        sp.add_argument("--commander", help="commander name, if the list doesn't mark it")
        sp.add_argument("--cache", help="card data cache file (default ~/.mtgsim/card_cache.json)")
        sp.add_argument("--offline", action="store_true", help="only use cached card data")
        sp.add_argument("--overrides", help="JSON file of tag fixes")
        sp.add_argument("--json", help="also save the results to this JSON file")

    sp = sub.add_parser("analyze", help="roles, opening-hand stats and goldfish games")
    common(sp)
    sp.add_argument("--games", type=int, default=3000, help="goldfish games (default 3000)")
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser("tags", help="show what the tagger thinks each card does")
    common(sp)
    sp.add_argument("--legend", action="store_true", help="explain every tag")
    sp.set_defaults(func=cmd_tags)

    sp = sub.add_parser("mulligan", help="keep or mulligan a 7-card hand")
    common(sp)
    sp.add_argument("--hand", required=True, help='7 card names separated by ";"')
    sp.add_argument("--mulligans", type=int, default=0, help="mulligans already taken (first one is free)")
    sp.add_argument("--sims", type=int, default=400, help="goldfish games with this hand (default 400)")
    sp.add_argument("--train", type=int, default=3000, help="random hands used to train the model")
    sp.set_defaults(func=cmd_mulligan)

    sp = sub.add_parser("combos", help="combos in the deck and combos one card away")
    common(sp)
    sp.add_argument("--limit", type=int, default=15)
    sp.add_argument("--refresh", action="store_true", help="ignore the saved combo lookup")
    sp.set_defaults(func=cmd_combos)

    sp = sub.add_parser("matchup", help="simulate games against opponents")
    common(sp)
    sp.add_argument("--vs", nargs="+", required=True,
                    help=f"one to three opponents: deck files or practice decks ({', '.join(ARCHETYPES)})")
    sp.add_argument("--games", type=int, default=300)
    sp.add_argument("--rounds", type=int, default=15, help="round limit per game")
    sp.add_argument("--impact", default="none",
                    help='measure cards: "defense", "all", "none", or names separated by ";"')
    sp.add_argument("--impact-games", type=int, default=300, help="games per card for --impact")
    sp.add_argument("--workers", type=int, default=1, help="CPU cores to use for --impact")
    sp.add_argument("--combos", action="store_true", help="let decks win with Commander Spellbook combos")
    sp.add_argument("--smart-mulligans", action="store_true", help="use the trained mulligan model for your deck")
    sp.add_argument("--log", type=int, metavar="SEED", help="print a play-by-play of one game first")
    sp.set_defaults(func=cmd_matchup)

    args = p.parse_args(argv)
    args.func(args)


if __name__ == "__main__":
    main()
