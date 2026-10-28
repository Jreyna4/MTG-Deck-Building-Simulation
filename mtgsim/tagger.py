"""Card tagger: reads each card's rules text and labels what it does.

Tags are short role names ("ramp", "removal", "attack_tax", ...). Numbers the simulator needs
(how much an attack tax costs, how many cards a spell draws, ...) go in ``card.params``.

The rules are regular expressions over Oracle text, so they cover the common wordings, not every
card ever printed. Fix a wrong tag with an override file (see ``load_overrides``).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .cards import Card
from .mana import ANY, land_sources, sources_from_text

NUM = {"a": 1, "an": 1, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7,
       "eight": 8, "nine": 9, "ten": 10, "x": 2, "that many": 2, "twice": 2}

TAG_INFO = {
    "land": "Land",
    "mdfc_land": "Spell that can be played as a land",
    "ramp": "Ramp: puts extra mana or lands into play",
    "mana_rock": "Mana rock (artifact that taps for mana)",
    "mana_dork": "Mana creature",
    "land_tutor": "Finds lands to your hand",
    "draw": "Card draw",
    "tutor": "Tutor: searches for a nonland card",
    "removal": "Removes a creature or permanent",
    "bounce": "Returns a permanent to hand",
    "edict": "Makes opponents sacrifice",
    "steal": "Takes control of opposing creatures",
    "board_wipe": "Board wipe",
    "counterspell": "Counterspell",
    "protection": "Protects your stuff (shield, indestructible, hexproof, phasing)",
    "attack_tax": "Attack tax: opponents pay mana to attack you",
    "attack_restriction": "Stops or limits attacks against you",
    "attack_deterrent": "Punishes or locks down creatures that attack you",
    "damage_prevention": "Prevents or delays damage to you",
    "fog": "One-shot: no combat damage / no attacks for a turn",
    "goad": "Goad: forces opposing creatures to attack someone else",
    "counters_on_opponents": "Puts counters on opponents' creatures",
    "proliferate": "Proliferate",
    "tapper": "Taps opposing creatures",
    "lockdown": "Stops a creature from attacking or blocking",
    "stax": "Slows opponents down",
    "lifegain": "Life gain",
    "copy_enchantment": "Copies your enchantments",
    "token_maker": "Makes creature tokens",
    "anthem": "Pumps your whole team",
    "pump": "Equipment/aura that pumps a creature",
    "counters": "+1/+1 counter synergy",
    "threat": "Big or evasive attacker",
    "drain": "Drains opponents' life",
    "wincon": "Alternate win condition",
    "creature": "Creature",
}

# Rough built-in value of each tag for keeping you alive. The matchup simulator measures the
# real effect; this is the quick estimate shown before any games are played.
DEFENSE_WEIGHTS = {
    "attack_tax": 3.0, "attack_restriction": 3.5, "damage_prevention": 3.5, "fog": 2.0,
    "goad": 2.5, "attack_deterrent": 2.0, "board_wipe": 2.5, "removal": 1.5, "edict": 1.0,
    "bounce": 0.8, "steal": 1.5, "tapper": 1.0, "lockdown": 1.5, "counterspell": 1.0,
    "protection": 0.7, "lifegain": 0.4, "stax": 0.7,
}

ROLE_GROUPS = [
    ("Lands", {"land"}),
    ("Ramp", {"ramp"}),
    ("Card draw & land tutors", {"draw", "land_tutor"}),
    ("Tutors", {"tutor"}),
    ("Removal", {"removal", "bounce", "edict", "steal"}),
    ("Board wipes", {"board_wipe"}),
    ("Counterspells", {"counterspell"}),
    ("Protection", {"protection"}),
    ("Pillow fort", {"attack_tax", "attack_restriction", "attack_deterrent", "damage_prevention", "fog"}),
    ("Goad & counters on opponents", {"goad", "counters_on_opponents", "proliferate", "tapper", "lockdown"}),
    ("Threats & win conditions", {"threat", "wincon", "drain", "token_maker"}),
]

DEFENSE_TAGS = set(DEFENSE_WEIGHTS)
REACTIVE_TAGS = {"counterspell", "fog"}

_BASIC_WORDS = {"plains": "W", "island": "U", "swamp": "B", "mountain": "R", "forest": "G"}


def _num(word: str | None, default: int = 1) -> int:
    if not word:
        return default
    w = word.strip().lower()
    if w.isdigit():
        return int(w)
    return NUM.get(w, default)


def _clean(card: Card, text: str) -> str:
    """Lower-case text with the card's own name replaced by ~ and reminder text removed."""
    names = set()
    for part in card.name.split(" // "):
        names.add(part)
        if "," in part:
            names.add(part.split(",")[0])
    t = text
    for n in sorted(names, key=len, reverse=True):
        if n:
            t = re.sub(rf"\b{re.escape(n)}\b", "~", t)
    t = re.sub(r"\([^)]*\)", "", t)
    return t.lower()


_ABILITY_WORD_RE = re.compile(r"^(?!spree|addendum|channel|suspend|buyback|equip|vanishing)[a-z' ]{3,25} — ")


def _abilities(text: str) -> list:
    """Split rules text into abilities. Ability words ("Landfall —") are dropped and each mode
    of a modal ability ("Choose one — • ...") keeps its header."""
    out = []
    head = ""
    for line in text.split("\n"):
        line = line.strip()
        if not line or line == "//":
            continue
        if line.startswith("•") or line.startswith("+ {"):
            out.append((head + " " + line.lstrip("•+ ").strip()).strip())
            continue
        line = _ABILITY_WORD_RE.sub("", line)
        if "•" in line:
            first, *modes = line.split("•")
            out.extend(first.strip() + " " + m.strip() for m in modes)
            continue
        head = line if line.endswith("—") or "choose one" in line or "choose one or more" in line \
            or line.startswith("spree") else ""
        out.append(line)
    return out


def _timing(ab: str, card: Card) -> tuple:
    """When an ability happens: (timing, activation cost or None)."""
    a = ab.strip()
    if a.startswith("at the beginning of your upkeep") or a.startswith("at the beginning of each upkeep"):
        return "upkeep", None
    if a.startswith("at the beginning of your end step") or a.startswith("at the beginning of each end step"):
        return "end", None
    if a.startswith("at the beginning of combat on your turn"):
        return "combat", None
    if re.match(r"(when|whenever) ~ enters|~ enters with|when ~ enters", a):
        return "etb", None
    if a.startswith("when ~ enters") or ("when ~ enters" in a and "and at the beginning" in a):
        return "etb", None
    if a.startswith("whenever") or a.startswith("landfall"):
        return "trigger", None
    m = re.match(r"^([^:\"]{1,60}):\s", a)
    if m and ("{" in m.group(1) or "remove" in m.group(1) or "sacrifice" in m.group(1)
              or "discard" in m.group(1)):
        if not m.group(1).startswith(("spree", "addendum", "channel", "cheer")):
            return "activated", m.group(1)
    if card.is_instant:
        return "instant", None
    if card.is_sorcery:
        return "sorcery", None
    if card.is_permanent and re.search(r"\bwhen ~ enters", a):
        return "etb", None
    return "static", None


def _cost_part(cost_text: str | None) -> str:
    if not cost_text:
        return ""
    return "".join(re.findall(r"\{[^}]*\}", cost_text)).replace("{t}", "").upper()


def tag_card(card: Card, deck_colors: frozenset = ANY) -> Card:
    """Fill ``card.tags`` and ``card.params`` from its rules text. Returns the card."""
    tags: set = set()
    p: dict = {}
    card.tags, card.params = tags, p

    if card.is_land:
        tags.add("land")
        _tag_land(card, deck_colors)
        return card
    if card.is_mdfc_land:
        tags.add("mdfc_land")
        p["back_mana"] = land_sources(card.back_type, card.back_text, None, deck_colors)
    if card.is_creature:
        tags.add("creature")

    low = _clean(card, card.text)
    abilities = _abilities(low)

    _tag_mana(card, abilities, deck_colors)
    _tag_land_search(card, abilities)
    _tag_draw(card, abilities)
    _tag_removal(card, abilities, low)
    _tag_wipes(card, abilities, low)
    _tag_defense(card, abilities, low)
    _tag_goad_and_counters(card, abilities, low)
    _tag_creature_stuff(card, abilities, low)
    _tag_misc(card, abilities, low)
    if p.get("hylda"):  # Hylda's modes are handled by the simulator as one trigger
        for k in ("draw_on_opp_tap", "draw_cast", "team_counters", "tokens"):
            p.pop(k, None)
        tags.discard("draw")
    return card


# --------------------------------------------------------------------------------------------
def _tag_land(card: Card, deck_colors) -> None:
    p = card.params
    low = _clean(card, card.text)
    p["mana"] = land_sources(card.front_type, card.text, card.produced_mana, deck_colors)
    m = re.search(r"enters (?:the battlefield )?tapped unless you control (\w+) or more other lands", low)
    if m:
        p["tapped_unless_lands"] = _num(m.group(1), 2)
    elif re.search(r"enters (?:the battlefield )?tapped(?! unless)", low):
        p["enters_tapped"] = True
    if "search your library for" in low and "land" in low and not card.is_basic:
        p["fetch"] = True
        p["enters_tapped"] = "tapped" in low.split("search your library")[1]


def _tag_mana(card: Card, abilities: list, deck_colors) -> None:
    if card.is_land or "Aura" in card.type_line:
        return
    found = []
    for ab in abilities:
        if '"' in ab:
            continue
        m = re.search(r"\{t\}(?:,[^:]*)?: add ([^.]*)", ab)
        if m:
            found.append(sources_from_text(m.group(1), deck_colors))
    found = [f for f in found if f]
    if not found:
        return
    if all(len(f) == 1 for f in found):
        union = frozenset().union(*[f[0] for f in found])
        mana = [union]
    else:
        mana = max(found, key=len)
    card.params["mana"] = mana
    card.tags.add("ramp")
    card.tags.add("mana_dork" if card.is_creature else "mana_rock")


def _tag_land_search(card: Card, abilities: list) -> None:
    tags, p = card.tags, card.params
    for ab in abilities:
        for sent in re.split(r"(?<=\.)\s+", ab):
            m = re.search(r"search(?:es)? (?:your|their) library for (?:up to )?(\w+) ([^.]*?)cards?", sent)
            if not m:
                continue
            what = m.group(2)
            colors = {c for w, c in _BASIC_WORDS.items() if w in what}
            if not ("land" in what or colors):
                if "~" not in what:
                    tags.add("tutor")
                    kind = next((k for k in ("enchantment", "creature", "artifact", "instant", "sorcery",
                                             "planeswalker") if k in what), "any")
                    p.setdefault("tutor", []).append(kind)
                continue
            n = _num(m.group(1))
            if "put one onto the battlefield" in sent and ("other into" in sent or "rest into" in sent):
                to_bf, to_hand = 1, n - 1
            elif "onto the battlefield" in sent:
                to_bf, to_hand = n, 0
            elif "into your hand" in sent or "into their hand" in sent:
                to_bf, to_hand = 0, n
            else:
                continue
            timing, cost = _timing(ab, card)
            entry = {"to_bf": to_bf, "to_hand": to_hand, "tapped": "battlefield tapped" in sent,
                     "colors": sorted(colors), "timing": timing, "cost": _cost_part(cost)}
            if timing == "activated" and "remove a +1/+1 counter" in (cost or ""):
                m2 = re.search(r"enters with (\w+) \+1/\+1 counters", " ".join(abilities))
                entry["uses"] = _num(m2.group(1) if m2 else None, 1)
            p.setdefault("land_search", []).append(entry)
            if to_bf:
                tags.add("ramp")
            if to_hand:
                tags.add("land_tutor")
    joined = " ".join(abilities)
    if "you may play an additional land" in joined:
        tags.add("ramp")
        p["extra_land"] = 1
    m = re.search(r"create (\w+) treasure tokens?", joined)
    if m:
        tags.add("ramp")
        p["treasure"] = _num(m.group(1))


def _tag_draw(card: Card, abilities: list) -> None:
    p = card.params
    for ab in abilities:
        timing, _ = _timing(ab, card)
        scale = 0.5 if "otherwise" in ab else 1.0
        for m in re.finditer(r"draws? (a|an|one|two|three|four|five|six|seven|x|that many) cards?", ab):
            before = ab[: m.start()].rstrip()
            if re.search(r"(opponent|controller|owner|that player|each player)$", before) or \
                    re.search(r"(opponent|controller|owner|that player) draws$", before + " draws"):
                continue
            n = _num(m.group(1)) * scale
            _add_draw(card, timing, n, ab)
        m = re.search(r"look at the top (\w+) cards of your library\. put (one|two|three) of them into your hand", ab)
        if m:
            _add_draw(card, timing, _num(m.group(2)) * scale, ab)


def _add_draw(card: Card, timing: str, n: float, ab: str) -> None:
    p = card.params
    card.tags.add("draw")
    if timing in ("instant", "sorcery", "static"):
        p["draw_cast"] = p.get("draw_cast", 0) + n
    elif timing == "etb":
        p["draw_etb"] = p.get("draw_etb", 0) + n
    elif timing in ("upkeep", "end", "combat"):
        p["draw_per_turn"] = p.get("draw_per_turn", 0) + n
    elif timing == "trigger":
        if "creature an opponent controls becomes tapped" in ab or "creatures your opponents control" in ab \
                or "untapped creature an opponent controls" in ab:
            p["draw_on_opp_tap"] = p.get("draw_on_opp_tap", 0) + n
            if "only once each turn" in ab:
                p["draw_on_opp_tap_once"] = True
        elif "counters on a creature you don't control" in ab:
            p["draw_on_opp_counters"] = p.get("draw_on_opp_counters", 0) + n
        else:
            # "whenever" triggers that depend on other players: count about half a card a turn
            p["draw_per_turn"] = p.get("draw_per_turn", 0) + 0.5 * n
    elif timing == "activated":
        p["draw_activated"] = p.get("draw_activated", 0) + n


_TARGET_KINDS = r"(nonland permanent|permanent|creature|artifact|enchantment|planeswalker)"


def _hit_types(phrase: str) -> set:
    phrase = phrase.lower()
    if "nonland permanent" in phrase or re.search(r"\bpermanent\b", phrase):
        return {"creature", "artifact", "enchantment", "planeswalker"}
    return {t for t in ("creature", "artifact", "enchantment", "planeswalker", "land") if t in phrase}


def _tag_removal(card: Card, abilities: list, low: str) -> None:
    tags, p = card.tags, card.params

    def add(kind, phrase, ab, **extra):
        timing, cost = _timing(ab, card)
        entry = {"kind": kind, "types": sorted(_hit_types(phrase)), "timing": timing, "cost": _cost_part(cost)}
        entry.update(extra)
        p.setdefault("removal", []).append(entry)

    for ab in abilities:
        for m in re.finditer(rf"(destroy|exile) target ([^.;]*?){_TARGET_KINDS}([^.;]*)", ab):
            mid, after = m.group(2), m.group(4)
            if "card" in mid or after.startswith(" card") or "you control" in (mid + after[:15]):
                continue
            extra = {}
            mt = re.search(r"toughness (\d+) or greater", after)
            if mt:
                extra["min_toughness"] = int(mt.group(1))
            add(m.group(1), m.group(2) + m.group(3) + after, ab, **extra)
            tags.add("removal")
        for m in re.finditer(rf"return target ([^.;]*?){_TARGET_KINDS}([^.;]*?) to its owner's hand", ab):
            if "you control" in m.group(0) or "card" in m.group(1):
                continue
            add("bounce", m.group(1) + m.group(2), ab)
            tags.add("bounce")
        m = re.search(r"target creature(?: an opponent controls)? gets -(\d+|x)/-(\d+|x)", ab)
        if m and _num(m.group(2), 2) >= 2:
            add("minus", "creature", ab, amount=_num(m.group(2), 2))
            tags.add("removal")
        m = re.search(r"deals? (\d+|x) damage to (any target|target creature)", ab)
        if m and "you control" not in ab[m.end(): m.end() + 15]:
            add("damage", "creature", ab, amount=_num(m.group(1), 3))
            tags.add("removal")
        m = re.search(r"(each opponent|target opponent|target player|each player) sacrifices (?:a|an) ([\w ]*?)"
                      r"(creature|artifact|enchantment|permanent)", ab)
        if m and "the rest" not in ab:
            add("edict", m.group(2) + m.group(3), ab, flying_only="flying" in ab[m.end(): m.end() + 15])
            tags.add("edict")
        if re.search(r"gain control of target", ab):
            add("steal", "creature", ab)
            tags.update({"steal", "removal"})
        if re.search(r"gain control of all creatures", ab) or re.search(r"gain control of that creature", ab):
            tags.add("steal")
        if "owner puts it on the top or bottom of their library" in ab:
            add("library", "creature", ab, each_opponent="for each opponent" in ab)
            tags.add("removal")
    # Aura "removal" (Pacifism, Imprisoned in the Moon...)
    if re.search(r"^enchant (creature|permanent|creature, land, or planeswalker)", low, re.M) and (
            "can't attack" in low or "is a colorless land" in low or "loses all abilities" in low
            or "loses all other card types and abilities" in low):
        p.setdefault("removal", []).append({"kind": "aura", "types": ["creature"], "timing": "sorcery", "cost": ""})
        tags.add("removal")


def _tag_wipes(card: Card, abilities: list, low: str) -> None:
    tags, p = card.tags, card.params
    for ab in abilities:
        m = re.search(r"(destroy|exile) all (other )?(tapped |untapped )?(nonland permanents|creatures|permanents)"
                      r"( your opponents control)?", ab)
        if m:
            p.setdefault("wipe", []).append({"kind": m.group(1), "one_sided": bool(m.group(5)),
                                              "state": (m.group(3) or "").strip()})
            tags.add("board_wipe")
        if re.search(r"return all (nonland permanents|creatures) to their owners' hands", ab):
            p.setdefault("wipe", []).append({"kind": "bounce", "one_sided": False, "state": ""})
            tags.add("board_wipe")
        if re.search(r"all creatures get -(\d+|x)/-(\d+|x)|deals? (\d+|x) damage to each creature", ab):
            p.setdefault("wipe", []).append({"kind": "minus", "one_sided": False, "state": ""})
            tags.add("board_wipe")
        if re.search(r"each player (?:chooses|puts [^.]* on) a creature they control and sacrifices the rest", ab) or \
                re.search(r"sacrifices the rest", ab):
            p.setdefault("wipe", []).append({"kind": "keep_one", "one_sided": False, "state": "",
                                              "kept_cant_attack_you": "can't attack you" in low})
            tags.add("board_wipe")


def _tag_defense(card: Card, abilities: list, low: str) -> None:
    tags, p = card.tags, card.params
    for ab in abilities:
        m = re.search(r"can't attack you(?: or (?:a )?planeswalkers? you control)? unless (?:its|their) controller "
                      r"pays \{(\d+|x)\}", ab)
        if m:
            amount = m.group(1)
            entry = {"amount": int(amount) if amount.isdigit() else 0}
            if amount == "x":
                if "number of enchantments you control" in ab:
                    entry["per"] = "enchantments"
                elif "number of counters on that creature" in ab:
                    entry["per"] = "counters"
                else:
                    entry["amount"] = 2
            if "with one or more counters on it" in ab or "counters on it" in ab[: m.start()]:
                entry["only_with_counters"] = True
            if "as long as ~ is untapped" in ab:
                entry["while_untapped"] = True
            p.setdefault("attack_tax", []).append(entry)
            tags.add("attack_tax")
            continue
        if re.search(r"until your next turn, creatures can't attack you", ab):
            p["fog_turn"] = True
            tags.add("fog")
        elif re.search(r"^creatures can't attack you\.?$", ab):
            p["no_attacks_you"] = True
            tags.add("attack_restriction")
        m = re.search(r"no more than (\w+) creatures? can attack you", ab)
        if m:
            p["max_attackers_you"] = _num(m.group(1))
            tags.add("attack_restriction")
        if re.search(r"creatures without flying can't attack", ab):
            p["moat"] = True
            tags.add("attack_restriction")
        if re.search(r"^creatures can't attack\.?$", ab):
            p["no_attacks_global"] = True
            tags.add("attack_restriction")
        if re.search(r"prevent all (?:combat )?damage that would be dealt to you", ab):
            if "this turn" in ab or card.is_instant or card.is_sorcery:
                p["fog"] = True
                tags.add("fog")
            else:
                p["prevent_all"] = True
                tags.add("damage_prevention")
        if re.search(r"prevent all combat damage that would be dealt this turn", ab):
            p["fog"] = True
            tags.add("fog")
        if "if damage would be dealt to you, put that many" in ab and "counters" in ab:
            p["delay_shield"] = True
            tags.add("damage_prevention")
        m = re.search(r"if a source would deal damage to you, prevent (\d+) of that damage", ab)
        if m:
            p["prevent_each"] = int(m.group(1))
            tags.add("damage_prevention")
        if re.search(r"creature an opponent controls becomes tapped, put an? \w+ counter on ~", ab) and \
                "prevent the next 1 damage" in low:
            p["accord"] = True
            tags.add("damage_prevention")
        if re.search(r"whenever a creature deals (?:combat )?damage to you, put an? \w+ counter on it", ab) and \
                ("defender" in low or "can't attack" in low):
            p["aurification"] = True
            tags.add("attack_deterrent")
        if re.search(r"whenever an opponent attacks you, choose target creature attacking you\. put a stun counter",
                     ab):
            p["stun_attacker"] = 1
            tags.add("attack_deterrent")
        if re.search(r"creatures with \w+ counters on them can't attack", ab):
            tags.add("lockdown")
        if re.search(r"you gain (\d+|x) life", ab):
            tags.add("lifegain")
        if re.search(r"(target creature|creatures?) you control gains? (?:hexproof|indestructible|protection)", ab) or \
                re.search(r"target creature gains (?:hexproof|indestructible)", ab) or \
                re.search(r"put a shield counter on (?:target creature(?! an opponent)|each of up to|"
                          r"a creature you control)", ab) or \
                re.search(r"permanents? you control (?:gain|phase out)|those permanents phase out", ab):
            tags.add("protection")
        if re.search(r"counter target ([\w, ]*?)spell", ab) and not ab.startswith("whenever"):
            m = re.search(r"counter target ([\w, ]*?)spell", ab)
            p["counter_types"] = m.group(1).strip() or "any"
            p["counter_soft"] = "unless its controller pays" in ab
            tags.add("counterspell")
        if re.search(r"creatures your opponents control enter tapped|spells your opponents cast cost \{\d\} more|"
                     r"can't cast noncreature spells with mana value greater", ab):
            tags.add("stax")
    m = re.search(r"suspend (\d+)\s*[—-]\s*((?:\{[^}]*\})+)", low)
    if m and "fog" in tags:
        p["suspend"] = {"turns": int(m.group(1)), "cost": m.group(2).upper()}
    if "exile ~ with three time counters on it" in low or re.search(r"exile ~ with (\w+) time counters on it", low):
        mm = re.search(r"exile ~ with (\w+) time counters on it", low)
        p["recur_every"] = _num(mm.group(1), 3) if mm else 3


def _tag_goad_and_counters(card: Card, abilities: list, low: str) -> None:
    tags, p = card.tags, card.params

    def add_counters(n, ab, any_target=False):
        timing, cost = _timing(ab, card)
        km = re.search(r"put an? ([\w+/-]+) counter", ab)
        kind = km.group(1) if km else ("+1/+1" if "support" in ab else "counter")
        p.setdefault("opp_counters", []).append({"n": n, "kind": kind, "timing": timing, "cost": _cost_part(cost),
                                                 "any_target": any_target,
                                                 "tap": "tap target creature" in ab or "tap that creature" in ab,
                                                 "stun": "stun counter" in ab,
                                                 "lock": "bribery" in ab or "can't attack or block" in low})
        tags.add("counters_on_opponents")

    if "goad" in low:
        tags.add("goad")
    for ab in abilities:
        if re.search(r"whenever you put one or more counters on a creature you don't control, tap that creature "
                     r"and goad it", ab):
            p["goad_on_counters"] = True
            continue
        if re.search(r"goad (each|all) creatures?", ab):
            p["goad_all"] = _timing(ab, card)[0]
        elif re.search(r"goad target creature", ab):
            p["goad_target"] = _timing(ab, card)[0]
        if re.search(r"put an? [\w+/-]+ counter on each creature (your opponents control|target player controls|"
                     r"target opponent controls)", ab):
            m = re.search(r"each creature (your opponents control|target player controls|target opponent controls)", ab)
            add_counters("all_opps" if m.group(1) == "your opponents control" else "all_one", ab)
        elif re.search(r"for each player, put an? [\w+/-]+ counter on up to one target creature that player controls",
                       ab) or re.search(r"for each opponent, put an? [\w+/-]+ counter on up to one target creature",
                                        ab):
            add_counters("one_each_opp", ab)
        elif re.search(r"put an? [\w+/-]+ counter on (?:up to (\w+) )?target creatures? (?:an opponent controls|"
                       r"you don't control)", ab) or re.search(r"tap target creature an opponent controls and put",
                                                               ab):
            m = re.search(r"up to (\w+) target", ab)
            add_counters(_num(m.group(1)) if m else 1, ab)
        elif re.search(r"put an? [\w+/-]+ counter on each of up to (\w+) (?:other )?target creatures", ab):
            m = re.search(r"each of up to (\w+)", ab)
            add_counters(_num(m.group(1)), ab, any_target=True)
        elif re.search(r"put an? [\w+/-]+ counter on target creature(?! you control)", ab) and \
                "target creature you control" not in ab:
            add_counters(1, ab, any_target=True)
        m = re.search(r"\bsupport (\d+)", ab)
        if m:
            add_counters(int(m.group(1)), ab, any_target=True)
        if "each player puts a vow counter" in ab:
            add_counters("all_opps", ab)
        if "proliferate" in ab:
            timing, cost = _timing(ab, card)
            kind = timing
            if timing == "trigger":
                if "whenever you cast a spell" in ab:
                    kind = "on_cast"
                elif "land you control enters" in ab or ab.startswith("landfall"):
                    kind = "on_land"
                elif "deals combat damage to a player" in ab:
                    kind = "on_combat_damage"
                else:
                    kind = "sometimes"
            p.setdefault("proliferate", []).append({"timing": kind, "cost": _cost_part(cost)})
            tags.add("proliferate")
        if re.search(r"tap target creature|tap all creatures your opponents control|tap up to \w+ target creatures",
                     ab) and "you control" not in ab.split("tap", 1)[1][:30]:
            timing, cost = _timing(ab, card)
            p.setdefault("tap_opp", []).append({"timing": timing, "cost": _cost_part(cost),
                                                "flying_ok": "without flying" not in ab})
            tags.add("tapper")
        if re.search(r"whenever you tap an untapped creature an opponent controls, you may pay \{1\}", ab):
            p["hylda"] = True
            tags.add("token_maker")


def _tag_creature_stuff(card: Card, abilities: list, low: str) -> None:
    tags, p = card.tags, card.params
    for ab in abilities:
        for m in re.finditer(r"create (a|an|one|two|three|four|five|six|x|that many) (\d+)/(\d+) ([^.]*?)"
                             r"creature tokens?", ab):
            if p.get("hylda"):
                continue
            timing, cost = _timing(ab, card)
            scale = 0.5 if "otherwise" in ab else 1.0
            desc = m.group(4) + ab[m.end(): m.end() + 40]
            p.setdefault("tokens", []).append({
                "n": _num(m.group(1)) * scale, "power": int(m.group(2)), "toughness": int(m.group(3)),
                "flying": "flying" in desc, "vigilance": "vigilance" in desc, "timing": timing,
                "cost": _cost_part(cost)})
            tags.add("token_maker")
        m = re.search(r"(other )?creatures you control get \+(\d+)/\+(\d+)", ab)
        if m and _timing(ab, card)[0] == "static":
            p["anthem"] = (int(m.group(2)), int(m.group(3)))
            tags.add("anthem")
        m = re.search(r"(?:equipped|enchanted) creature gets \+(\d+)/\+(\d+)", ab)
        if m:
            p["pump"] = (int(m.group(1)), int(m.group(2)))
            tags.add("pump")
            if "flying" in ab:
                p["pump_flying"] = True
        m = re.search(r"^equip \{(\d+)\}", ab)
        if m:
            p["equip"] = int(m.group(1))
        if re.search(r"put a \+1/\+1 counter on each creature you control", ab):
            timing, _ = _timing(ab, card)
            p["team_counters"] = timing
            tags.add("counters")
        elif "+1/+1 counter" in ab:
            tags.add("counters")
    if card.is_creature:
        kws = {k.lower() for k in card.keywords}
        p["flying"] = "flying" in kws or bool(re.search(r"^flying", low, re.M))
        p["trample"] = "trample" in kws
        p["vigilance"] = "vigilance" in kws
        p["menace"] = "menace" in kws
        p["haste"] = "haste" in kws
        p["defender"] = "defender" in kws
        p["infect"] = "infect" in kws
        p["unblockable"] = "~ can't be blocked" in low
        m = re.search(r"enters with (\w+) \+1/\+1 counters", low)
        p["etb_counters"] = _num(m.group(1)) if m else 0
        power = card.power_int + p["etb_counters"]
        if power >= 4 or (power >= 3 and (p["flying"] or p["unblockable"])):
            tags.add("threat")


def _tag_misc(card: Card, abilities: list, low: str) -> None:
    tags, p = card.tags, card.params
    if "you win the game" in low:
        tags.add("wincon")
    for ab in abilities:
        m = re.search(r"each opponent loses (\d+|x) life|deals? (\d+|x) damage to each opponent", ab)
        if m:
            timing, _ = _timing(ab, card)
            p.setdefault("drain", []).append({"amount": _num(m.group(1) or m.group(2), 2), "timing": timing})
            tags.add("drain")
        if re.search(r"enter as a copy of an enchantment you control", ab):
            p["copy_ench"] = {"timing": "etb", "cost": ""}
            tags.add("copy_enchantment")
        elif re.search(r"create a token that's a copy of (?:it|target enchantment)", ab) and "enchantment" in ab:
            timing, cost = _timing(ab, card)
            p["copy_ench"] = {"timing": timing, "cost": _cost_part(cost)}
            tags.add("copy_enchantment")


# --------------------------------------------------------------------------------------------
def defense_rating(card: Card) -> float:
    """Quick built-in estimate of how much a card helps keep you alive (0 = not at all)."""
    score = sum(w for t, w in DEFENSE_WEIGHTS.items() if t in card.tags)
    for tax in card.params.get("attack_tax", []):
        score += 0.5 * (tax.get("amount") or 2)
    if card.params.get("goad_on_counters"):
        score += 2.0
    return round(score, 1)


def load_overrides(path: str | Path | None) -> dict:
    """Read a JSON file of tag fixes::

        {"Card Name": {"add": ["removal"], "remove": ["threat"], "params": {"draw_cast": 2}}}
    """
    if not path:
        return {}
    path = Path(path)
    if not path.exists():
        return {}
    return {k.lower(): v for k, v in json.loads(path.read_text(encoding="utf-8")).items()}


def apply_override(card: Card, overrides: dict) -> None:
    o = overrides.get(card.name.lower()) or overrides.get(card.name.split(" // ")[0].lower())
    if not o:
        return
    card.tags.update(o.get("add", []))
    card.tags.difference_update(o.get("remove", []))
    card.params.update(o.get("params", {}))
