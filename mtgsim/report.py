"""Plain-text formatting for the command line."""
from __future__ import annotations

from .tagger import ROLE_GROUPS, TAG_INFO, defense_rating


def pct(x: float | None) -> str:
    return "  -  " if x is None else f"{100 * x:5.1f}%"


def table(rows: list, headers: list, aligns: str | None = None) -> str:
    cells = [[str(h) for h in headers]] + [[str(c) for c in r] for r in rows]
    widths = [max(len(r[i]) for r in cells) for i in range(len(headers))]
    aligns = aligns or "l" * len(headers)
    out = []
    for n, r in enumerate(cells):
        out.append("  ".join(c.ljust(w) if a == "l" else c.rjust(w) for c, w, a in zip(r, widths, aligns)))
        if n == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def heading(text: str) -> str:
    return f"\n{text}\n{'=' * len(text)}"


def deck_overview(deck) -> str:
    lines = [heading(f"Deck: {deck.name}")]
    cmdr = ", ".join(c.name for c in deck.commanders) or "(none)"
    lines.append(f"Commander: {cmdr}   Colors: {''.join(sorted(deck.colors))}   "
                 f"Cards: {len(deck.cards) + len(deck.commanders)}   Lands: {deck.land_count}")
    if deck.not_found:
        lines.append("Cards not found on Scryfall (check spelling): " + ", ".join(deck.not_found))
    for w in deck.warnings:
        lines.append("Note: " + w)
    rows = []
    for title, tagset in ROLE_GROUPS:
        names = [c.name for c, _ in deck.unique() if c.tags & tagset]
        count = sum(n for c, n in deck.unique() if c.tags & tagset)
        if count:
            shown = ""
            for i, n in enumerate(names):
                extra = (", " if shown else "") + n
                if len(shown) + len(extra) > 60:
                    shown += f" +{len(names) - i} more"
                    break
                shown += extra
            rows.append((title, count, shown if title != "Lands" else ""))
    lines.append("")
    lines.append(table(rows, ["Role", "Cards", "Examples"], "lrl"))
    return "\n".join(lines)


def tag_table(deck) -> str:
    rows = []
    for c, n in sorted(deck.unique(), key=lambda p: (p[0].is_land, p[0].cmc, p[0].name)):
        tags = sorted(c.tags - {"creature", "land"})
        rows.append((c.name[:38], n, int(c.cmc), ", ".join(tags) or ("land" if c.is_land else "-"),
                     defense_rating(c) or ""))
    for c in deck.commanders:
        rows.insert(0, (c.name[:38] + " (commander)", 1, int(c.cmc), ", ".join(sorted(c.tags - {"creature"})),
                        defense_rating(c) or ""))
    return heading("Card roles") + "\n" + table(rows, ["Card", "#", "MV", "Tags", "Defense"], "lrrlr") + \
        "\n\nDefense = quick built-in estimate. Run `matchup --impact` to measure it in simulated games."


def tag_legend() -> str:
    return "\n".join(f"  {k:22} {v}" for k, v in TAG_INFO.items())


def hand_report(stats: dict, gold: dict) -> str:
    L = [heading("Opening hands")]
    L.append(f"{stats['lands']} lands in {stats['deck_size']} cards; average {stats['avg_lands_in_7']:.2f} lands "
             f"in an opening 7; average mana value of spells {stats['avg_mana_value']:.2f}.")
    dist = stats["land_distribution"]
    L.append("Lands in opening 7:  " + "  ".join(f"{k}: {pct(v).strip()}" for k, v in dist.items() if v >= 0.0005))
    L.append("")
    L.append(table([(k, pct(v)) for k, v in stats["hand_flags"].items()], ["Opening 7 has...", "Chance"], "lr"))
    L.append("")
    rows = [(r["role"], r["count"], pct(r["opening_7"]), pct(r["turn_2"]), pct(r["turn_4"]), pct(r["turn_6"]))
            for r in stats["by_role"]]
    L.append("Chance to have at least one card of each role (on the draw):")
    L.append(table(rows, ["Role", "Cards", "Opening 7", "By turn 2", "By turn 4", "By turn 6"], "lrrrrr"))
    L.append("")
    rows = [(r["turn"], pct(r["on_play"]), pct(r["on_draw"])) for r in stats["land_drops"]]
    L.append("Chance to hit every land drop from lands alone (no ramp):")
    L.append(table(rows, ["Turn", "On the play", "On the draw"], "rrr"))
    one = stats["single_card"]
    L.append("\nChance to have a specific 1-of card: " + ", ".join(
        f"{r['turn'] if isinstance(r['turn'], str) else 'turn ' + str(r['turn'])} {pct(r['chance']).strip()}"
        for r in one))
    L.append(heading("Goldfish games (playing alone)"))
    L.append(f"{gold['games']} games. Score = mana put to use in turns 1-5 (spent, or held up for instants). "
             f"Average {gold['avg_score']:.1f}; middle half of games {gold['score_percentiles'][25]}-"
             f"{gold['score_percentiles'][75]}.")
    if gold.get("mulligans"):
        m = gold["mulligans"]
        total_m = sum(m.values())
        L.append("Mulligans (keep 3-5 lands, or 2 with ramp): " + ", ".join(
            f"{k} mulligan{'s' if k != 1 else ''} {pct(v / total_m).strip()}" for k, v in m.items()))
    rows = [(t["turn"], f"{t['lands']:.2f}", f"{t['mana']:.2f}", f"{t['used']:.2f}", f"{t['defense']:.2f}")
            for t in gold["turns"]]
    L.append(table(rows, ["Turn", "Lands", "Mana available", "Mana used", "Defense pieces out"], "rrrrr"))
    ct = gold["commander_turn"]
    total = sum(ct.values())
    cmd = ", ".join(f"turn {k}: {pct(v / total).strip()}" for k, v in ct.items() if k) + \
        f", not by turn {len(gold['turns'])}: {pct(ct.get(0, 0) / total).strip()}"
    L.append("Commander first cast: " + cmd)
    cards = [c for c in gold["cards"] if c["in_opener"] >= max(20, gold["games"] * 0.02)]
    spells = [c for c in cards if not c["is_land"]]
    L.append("\nBest cards to see in your opening hand (score when in the opening 7 vs. not):")
    L.append(table([(c["name"][:36], f"{c['lift']:+.2f}", pct(c["stuck_rate"])) for c in spells[:12]],
                   ["Card", "Score lift", "Stuck in hand"], "lrr"))
    L.append("\nCards that slow your opening hand down the most:")
    L.append(table([(c["name"][:36], f"{c['lift']:+.2f}", pct(c["stuck_rate"])) for c in spells[-12:][::-1]],
                   ["Card", "Score lift", "Stuck in hand"], "lrr"))
    L.append("Stuck in hand = drawn by turn 3 but still in hand at the end of turn 5. Removal, counters and fogs\n"
             "often look 'stuck' here because a goldfish game has no opponents to use them on.")
    return "\n".join(L)


def mulligan_report(r: dict) -> str:
    L = [heading(f"Mulligan advice: {r['decision'].upper()}")]
    L.append("Hand kept: " + ", ".join(r["keep"]))
    if r["bottom"]:
        L.append("Put on the bottom: " + ", ".join(r["bottom"]))
    L.append(f"Expected goldfish score with this hand: {r['expected_score']:.1f} "
             f"(better than {r['percentile']:.0f}% of random opening 7s)")
    if r["mulligan_value"] is not None:
        L.append(f"Expected score if you mulligan to {r['cards_after_mulligan']}: {r['mulligan_value']:.1f} "
                 f"(difference {r['margin']:+.1f})")
    L.append("\nWhy:")
    L.extend("  - " + x for x in r["reasons"])
    L.append("\nWhat the model weighed most for this hand:")
    L.extend(f"  {name:22} {eff:+.2f}" for name, eff in r["contributions"])
    return "\n".join(L)


def combos_report(res: dict, limit: int = 15) -> str:
    L = [heading("Combos (Commander Spellbook)")]
    inc = res["included"]
    L.append(f"{len(inc)} combo(s) fully in your deck; {len(res['almost'])} more are one or two cards away.")
    for c in inc[:limit]:
        L.append(f"\n* {' + '.join(c['cards'])}" + (" + " + ", ".join(c["templates"]) if c["templates"] else ""))
        L.append(f"  Result: {'; '.join(c['results'][:4])}")
        if c["mana_needed"]:
            L.append(f"  Mana needed: {c['mana_needed']}")
        L.append(f"  {c['url']}")
    if res["best_additions"]:
        L.append("\nCards that would add the most combos (each completes these with what you already run):")
        L.append(table([(a["card"], a["combos"], a["winning_combos"]) for a in res["best_additions"][:10]],
                       ["Card to add", "Combos", "That win"], "lrr"))
    near = [c for c in res["almost"] if len(c["missing"]) == 1][:limit]
    if near:
        L.append("\nOne card away:")
        for c in near:
            L.append(f"* add {c['missing'][0]}: {' + '.join(c['cards'])} -> {'; '.join(c['results'][:2])}")
    return "\n".join(L)


def matchup_report(r: dict) -> str:
    L = [heading("Matchup: you vs " + ", ".join(r["opponents"]))]
    L.append(f"{r['games']} games, up to 15 rounds each.")
    rows = [
        ("Win rate", pct(r["win_rate"]), f"fair share with {len(r['opponents']) + 1} players is {pct(r['fair_share']).strip()}"),
        ("Still alive at the end", pct(r["survival_rate"]), ""),
        ("Average round you died", f"{r['avg_death_round']:.1f}" if r["avg_death_round"] else "-", ""),
        ("Damage taken per game", f"{r['avg_damage_taken']:.1f}", ""),
        ("Damage prevented per game", f"{r['avg_damage_prevented']:.1f}", "prevention and fogs"),
        ("Attack tax opponents paid", f"{r['avg_tax_collected']:.1f}", "mana per game"),
        ("Times attacked per game", f"{r['avg_times_attacked']:.1f}", f"{r['avg_attackers_faced']:.1f} attackers"),
        ("Mulligans per game", f"{r.get('avg_mulligans', 0):.2f}", ""),
    ]
    L.append(table(rows, ["", "Value", "Note"], "lrl"))
    L.append("Game results: " + ", ".join(f"{k}: {v}" for k, v in r["outcomes"].items()))
    return "\n".join(L)


def impact_report(r: dict, limit: int | None = None) -> str:
    L = [heading("Card impact (each card replaced by a blank, same games replayed)")]
    L.append(f"{r['games_per_card']} games per card. Numbers are for games where you drew the card. "
             "'±' is the noise; treat differences under about twice the noise as luck.")
    rows = []
    for c in r["cards"][:limit]:
        clear = abs(c["win_delta"] + c["survival_delta"]) > 2 * max(c["win_noise"], c["survival_noise"], 1e-9)
        rows.append((c["name"][:34], pct(c["drawn_rate"]).strip(),
                     f"{c['defense_score']:+.1f} ±{c['defense_noise']:.1f}",
                     f"{100 * c['survival_delta']:+.1f} ±{100 * c['survival_noise']:.1f}",
                     f"{100 * c['win_delta']:+.1f} ±{100 * c['win_noise']:.1f}",
                     "yes" if clear else ""))
    L.append(table(rows, ["Card", "Drawn", "Damage saved", "Survival % pts", "Win % pts", "Clear?"], "lrrrrl"))
    return "\n".join(L)
