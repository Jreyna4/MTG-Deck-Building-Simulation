"""A simplified Commander game engine.

It is not a full rules engine. It plays the parts of Magic that decide how a deck develops and
how combat goes: land drops, ramp, casting spells by priority, card draw, creatures and tokens,
attacks and blocks, attack taxes, damage prevention, goad, counters on opposing creatures,
proliferate, removal, board wipes, counterspells and fogs. Card behavior comes from the tags in
``tagger.py``; cards with abilities it doesn't model still take up a slot and cost mana.

The same engine runs solo "goldfish" games (one player, no opponents) and multiplayer matchups.
"""
from __future__ import annotations

import random
from collections import defaultdict

from .cards import Card
from .mana import ANY, ManaCost, choose_payment, parse_mana_cost

STARTING_LIFE = 40
TAX_VALUE = 1.0  # how many points of damage one mana of attack tax is "worth" to an attacker
PILLOW_TAGS = {"attack_tax", "attack_restriction", "damage_prevention", "attack_deterrent"}
_COLOR_OF_BASIC = {"Plains": "W", "Island": "U", "Swamp": "B", "Mountain": "R", "Forest": "G"}


def bottom_by_rule(hand: list, n: int) -> tuple:
    hand = list(hand)
    bottom = []
    for _ in range(n):
        lands = [c for c in hand if c.is_land]
        spells = [c for c in hand if not c.is_land]
        pick = lands[-1] if (len(lands) > 4 or not spells) else max(spells, key=lambda c: c.cmc)
        hand.remove(pick)
        bottom.append(pick)
    return hand, bottom


def heuristic_mulligan(game, pl, hand: list, m: int):
    """Default mulligan rule: keep 3-5 lands, or 2 lands with cheap ramp. Never go below 5 cards
    on purpose. Returns (kept, bottomed) or None to mulligan."""
    n_bottom = max(0, m - 1)
    lands = sum(1 for c in hand if c.is_land)
    ramp = sum(1 for c in hand if not c.is_land and "ramp" in c.tags and c.cmc <= 3)
    ok = 3 <= lands <= 5 or (lands == 2 and ramp >= 1)
    if ok or m >= 3:
        return bottom_by_rule(hand, n_bottom)
    return None


def cmd_key(c) -> str:
    """Commander damage is tracked per commander card, not per time it was cast."""
    return f"{c.owner}:{c.name}"


# =============================================================================================
class Creature:
    __slots__ = ("card", "name", "owner", "base_p", "base_t", "is_token", "is_commander", "counters",
                 "tapped", "sick", "goaded_by", "cant_attack", "cant_block", "no_attack_vs", "flying",
                 "trample", "vigilance", "menace", "unblockable", "infect", "defender", "bonus_p", "bonus_t",
                 "damage", "attacked_last", "uid")

    _next = 0

    def __init__(self, card: Card | None, owner: int, power: int, toughness: int, is_token=False,
                 is_commander=False, flying=False, vigilance=False):
        Creature._next += 1
        self.uid = Creature._next
        self.card = card
        self.name = card.name if card else f"{power}/{toughness} token"
        self.owner = owner
        self.base_p, self.base_t = power, toughness
        self.is_token, self.is_commander = is_token, is_commander
        self.counters = defaultdict(int)
        p = card.params if card else {}
        self.flying = flying or p.get("flying", False)
        self.trample = p.get("trample", False)
        self.vigilance = vigilance or p.get("vigilance", False)
        self.menace = p.get("menace", False)
        self.unblockable = p.get("unblockable", False)
        self.infect = p.get("infect", False)
        self.defender = p.get("defender", False)
        self.tapped = False
        self.sick = not p.get("haste", False)
        self.goaded_by = set()
        self.cant_attack = self.defender
        self.cant_block = False
        self.no_attack_vs = set()
        self.bonus_p = self.bonus_t = 0
        self.damage = 0
        self.attacked_last = set()

    def power(self, anthem=(0, 0)) -> int:
        return max(0, self.base_p + self.counters["+1/+1"] - self.counters["-1/-1"] + self.bonus_p + anthem[0])

    def toughness(self, anthem=(0, 0)) -> int:
        return self.base_t + self.counters["+1/+1"] - self.counters["-1/-1"] + self.bonus_t + anthem[1]

    def total_counters(self) -> int:
        return sum(v for v in self.counters.values() if v > 0)

    def add_counter(self, kind: str, n: int = 1) -> None:
        if kind == "feather":
            self.base_p, self.base_t, self.flying = 3, 1, True
        if kind in ("bribery",):
            self.cant_attack = self.cant_block = True
        self.counters[kind] += n


class Player:
    def __init__(self, idx: int, name: str, cards: list, commanders: list, colors: frozenset, rng: random.Random,
                 shuffle: bool = True):
        self.idx, self.name, self.rng = idx, name, rng
        self.colors = colors or ANY
        self.library = list(cards)
        if shuffle:
            rng.shuffle(self.library)
        self.hand: list = []
        self.graveyard: list = []
        self.lands: list = []          # [card, mana units]
        self.rocks: list = []          # [card, mana units]
        self.creatures: list = []
        self.permanents: list = []     # other nonland permanents (enchantments, artifacts...)
        self.command_zone = list(commanders)
        self.commander_names = {c.name for c in commanders}
        self.cmd_casts = defaultdict(int)
        self.life = STARTING_LIFE
        self.poison = 0
        self.cmd_damage = defaultdict(int)
        self.alive = True
        self.died_round = None
        self.pool: list = []
        self.land_drops = 1
        self.turn = 0
        self.delay_counters = 0
        self.accord_counters = 0
        self.suspended: list = []      # [card, turns left]
        self.no_attacks_until_my_turn = False
        self.used_this_turn: set = set()
        self.ability_uses = defaultdict(int)
        self.drew_from_tap_this_turn = False
        self.attacked_by_last_round: set = set()
        self.attacked_by_this_round: set = set()
        # stats
        self.mana_spent = 0
        self.spells_cast = 0
        self.damage_taken = 0
        self.damage_prevented = 0
        self.tax_collected = 0
        self.times_attacked = 0
        self.attackers_faced = 0
        self.cmd_cast_turn = None
        self.mulligans = 0
        self.cards_seen: set = set()

    # ---- zones ------------------------------------------------------------------------
    def draw(self, n: int = 1) -> None:
        for _ in range(int(n)):
            if not self.library:
                self.alive = False
                return
            card = self.library.pop()
            self.hand.append(card)
            self.cards_seen.add(card.name)

    def draw_fraction(self, x: float) -> None:
        whole = int(x)
        if self.rng.random() < x - whole:
            whole += 1
        self.draw(whole)

    def all_permanent_cards(self) -> list:
        return [c.card for c in self.creatures if c.card is not None] + self.permanents + [r[0] for r in self.rocks]

    def enchantment_count(self) -> int:
        return sum(1 for c in self.permanents if c.is_enchantment) + \
            sum(1 for r in self.rocks if r[0].is_enchantment) + \
            sum(1 for c in self.creatures if c.card is not None and c.card.is_enchantment)

    def has_param(self, key: str) -> bool:
        return any(key in c.params for c in self.all_permanent_cards())

    def anthem(self) -> tuple:
        p = t = 0
        for c in self.all_permanent_cards():
            if "anthem" in c.params:
                p += c.params["anthem"][0]
                t += c.params["anthem"][1]
        return p, t

    def board_power(self) -> int:
        a = self.anthem()
        return sum(c.power(a) for c in self.creatures if not c.cant_attack)

    def defense_pieces(self) -> int:
        return sum(1 for c in self.all_permanent_cards() if c.tags & PILLOW_TAGS or c.params.get("goad_on_counters"))

    # ---- mana ---------------------------------------------------------------------------
    def refresh_pool(self) -> None:
        self.pool = []
        for _, units in self.lands:
            self.pool.extend(units)
        for _, units in self.rocks:
            self.pool.extend(units)
        for c in self.creatures:
            if not c.sick and c.card is not None and "mana" in c.card.params and not c.tapped:
                self.pool.extend(c.card.params["mana"])

    def can_afford(self, cost: ManaCost, extra: int = 0) -> bool:
        return choose_payment(cost, self.pool, extra=extra) is not None

    def pay(self, cost: ManaCost, extra: int = 0) -> bool:
        idx = choose_payment(cost, self.pool, extra=extra)
        if idx is None:
            return False
        for i in sorted(idx, reverse=True):
            self.pool.pop(i)
        self.mana_spent += len(idx)
        return True

    def pay_generic(self, n: int) -> bool:
        if n <= 0:
            return True
        return self.pay(ManaCost(generic=n))


# =============================================================================================
class Game:
    """One game. ``players`` is a list of (name, cards, commanders, colors)."""

    def __init__(self, decks: list, seed: int | None = None, max_rounds: int = 15, combos: list | None = None,
                 first: int | None = None, goldfish: bool = False, verbose: bool = False,
                 mulligans: bool = True, policies: dict | None = None):
        self.rng = random.Random(seed)
        self.players = [Player(i, name, cards, cmdrs, colors, self.rng) for i, (name, cards, cmdrs, colors)
                        in enumerate(decks)]
        self.max_rounds = max_rounds
        self.round = 0
        self.goldfish = goldfish or len(self.players) == 1
        self.combos = combos or []
        start = first if first is not None else self.rng.randrange(len(self.players))
        self.order = self.players[start:] + self.players[:start]
        self.winner = None
        self.mulligans = mulligans
        self.policies = policies or {}
        self.verbose = verbose
        self.events: list = []
        self.first_player_skips_draw = True
        self.log_turns: list = []  # per (round, player idx) snapshot for goldfish stats
        self.active = None

    # ---- helpers ------------------------------------------------------------------------
    def say(self, msg: str) -> None:
        if self.verbose:
            self.events.append(f"R{self.round} {msg}")

    def opponents(self, pl: Player) -> list:
        return [q for q in self.players if q is not pl and q.alive]

    def alive_players(self) -> list:
        return [q for q in self.players if q.alive]

    def check_deaths(self) -> None:
        for q in self.players:
            if q.alive and (q.life <= 0 or q.poison >= 10 or any(v >= 21 for v in q.cmd_damage.values())):
                self.eliminate(q)
        alive = self.alive_players()
        if not self.goldfish and len(alive) == 1 and self.winner is None:
            self.winner = alive[0].idx

    def eliminate(self, q: Player) -> None:
        worst_cmd = max(q.cmd_damage.values(), default=0)
        self.say(f"*** {q.name} is eliminated (life {q.life}, poison {q.poison}, commander damage {worst_cmd})")
        q.alive = False
        q.died_round = self.round
        q.creatures, q.permanents, q.rocks, q.lands = [], [], [], []
        for other in self.players:
            for c in other.creatures:
                c.goaded_by.discard(q.idx)

    # ---- running ------------------------------------------------------------------------
    def setup(self, hands: dict | None = None, mulligan=None) -> None:
        """Draw opening hands. ``hands`` maps player index -> list of cards to start with.
        Other players draw 7 and mulligan (London rules, first mulligan free) with their policy."""
        for q in self.players:
            if hands and q.idx in hands:
                wanted = list(hands[q.idx])
                for card in wanted:
                    q.library.remove(card)
                q.hand = wanted
                q.cards_seen.update(c.name for c in wanted)
            else:
                q.draw(7)
                policy = mulligan or self.policies.get(q.idx)
                if policy is None and self.mulligans and not self.goldfish:
                    policy = heuristic_mulligan
                if policy:
                    self.london_mulligan(q, policy)

    def london_mulligan(self, q: Player, policy) -> None:
        m = 0
        while True:
            choice = policy(self, q, list(q.hand), m)
            if choice is not None:
                keep, bottom = choice
                for c in bottom:
                    q.hand.remove(c)
                    q.library.insert(0, c)
                q.mulligans = m
                q.cards_seen = {c.name for c in q.hand}
                self.say(f"{q.name} keeps {len(q.hand)} after {m} mulligan(s)")
                return
            q.library.extend(q.hand)
            q.hand = []
            self.rng.shuffle(q.library)
            q.draw(7)
            m += 1

    def run(self, hands: dict | None = None, mulligan=None, setup: bool = True) -> "Game":
        if setup:
            self.setup(hands, mulligan)
        for self.round in range(1, self.max_rounds + 1):
            for pl in self.order:
                if not pl.alive:
                    continue
                self.take_turn(pl)
                self.check_deaths()
                if self.winner is not None:
                    return self
            for q in self.players:
                q.attacked_by_last_round = q.attacked_by_this_round
                q.attacked_by_this_round = set()
        return self

    def take_turn(self, pl: Player) -> None:
        self.active = pl
        pl.turn += 1
        first_turn_of_game = self.round == 1 and pl is self.order[0] and self.first_player_skips_draw
        # untap
        for c in pl.creatures:
            c.sick = False
            if c.counters["stun"] > 0 and c.tapped:
                c.counters["stun"] -= 1
            else:
                c.tapped = False
            c.damage = 0
        for q in self.players:
            for c in q.creatures:
                c.goaded_by.discard(pl.idx)
        pl.no_attacks_until_my_turn = False
        pl.used_this_turn = set()
        pl.drew_from_tap_this_turn = False
        pl.land_drops = 1 + sum(c.params.get("extra_land", 0) for c in pl.all_permanent_cards())
        pl.refresh_pool()
        spent_before = pl.mana_spent
        # upkeep
        self.upkeep(pl)
        if not pl.alive:
            return
        # draw
        if not first_turn_of_game:
            pl.draw(1)
            if not pl.alive:
                return
        # main 1
        reserve = 0 if self.goldfish else self.plan_attack_reserve(pl)
        self.play_land(pl)
        self.main_phase(pl, reserve)
        self.check_combo_win(pl)
        if self.winner is not None:
            return
        # combat
        if not self.goldfish:
            self.combat(pl)
            self.check_deaths()
            if self.winner is not None or not pl.alive:
                return
        # main 2
        self.play_land(pl)
        self.main_phase(pl, self.instant_reserve(pl))
        self.use_activated(pl)
        self.check_combo_win(pl)
        # end step
        self.end_step(pl)
        self.discard_to_hand_size(pl)
        if self.goldfish:
            held = self.held_value(pl)
            self.log_turns.append({
                "turn": pl.turn, "lands": len(pl.lands), "mana": len(pl.pool) + pl.mana_spent - spent_before,
                "spent": pl.mana_spent - spent_before, "held": held, "defense": pl.defense_pieces(),
                "cmd": pl.cmd_cast_turn, "hand": [c.name for c in pl.hand], "spells": pl.spells_cast})

    # ---- upkeep / end step -------------------------------------------------------------
    def upkeep(self, pl: Player) -> None:
        # Delaying Shield: pay {1}{W} per delayed point or lose that life
        if pl.delay_counters:
            n = pl.delay_counters
            pl.delay_counters = 0
            paid = 0
            cost = parse_mana_cost("{1}{W}")
            while paid < n and pl.pay(cost):
                paid += 1
            self.lose_life(pl, n - paid)
        # suspended cards
        for entry in list(pl.suspended):
            entry[1] -= 1
            if entry[1] <= 0:
                pl.suspended.remove(entry)
                self.resolve(pl, entry[0], free=True)
        for card in list(pl.all_permanent_cards()):
            P = card.params
            for ls in P.get("land_search", []):
                if ls["timing"] == "upkeep" and self.land_tax_condition(pl):
                    self.search_lands(pl, ls)
            for oc in P.get("opp_counters", []):
                if oc["timing"] == "upkeep":
                    self.place_opp_counters(pl, oc)
            if P.get("draw_per_turn") and self._ability_timing(card, "draw") == "upkeep":
                pl.draw_fraction(P["draw_per_turn"])
            for tok in P.get("tokens", []):
                if tok["timing"] == "upkeep":
                    self.make_tokens(pl, tok)
            for d in P.get("drain", []):
                if d["timing"] == "upkeep":
                    self.drain(pl, d["amount"])

    @staticmethod
    def _ability_timing(card: Card, what: str) -> str:
        t = card.text.lower()
        if what == "draw" and "at the beginning of your upkeep" in t and "draw" in t.split("upkeep", 1)[1][:200]:
            return "upkeep"
        return "end"

    def end_step(self, pl: Player) -> None:
        for card in list(pl.all_permanent_cards()):
            P = card.params
            if P.get("draw_per_turn") and self._ability_timing(card, "draw") == "end":
                pl.draw_fraction(P["draw_per_turn"])
            for tok in P.get("tokens", []):
                if tok["timing"] == "end":
                    self.make_tokens(pl, tok)
            for oc in P.get("opp_counters", []):
                if oc["timing"] == "end":
                    self.place_opp_counters(pl, oc)
            if P.get("team_counters") == "end":
                for c in pl.creatures:
                    c.add_counter("+1/+1")
                    c.tapped = False
            for r in P.get("removal", []):
                if r["timing"] == "end" and r.get("each_opponent"):
                    for q in self.opponents(pl):
                        marked = [c for c in q.creatures if c.counters["rejection"] > 0]
                        if marked:
                            self.remove_creature(max(marked, key=lambda c: self.threat_score(pl, c)))
            for d in P.get("drain", []):
                if d["timing"] == "end":
                    self.drain(pl, d["amount"])

    def land_tax_condition(self, pl: Player) -> bool:
        if self.goldfish:
            return len(pl.lands) < pl.turn
        return any(len(q.lands) > len(pl.lands) for q in self.opponents(pl))

    # ---- lands --------------------------------------------------------------------------
    def play_land(self, pl: Player) -> None:
        while pl.land_drops > 0:
            lands = [c for c in pl.hand if c.is_land]
            if not lands:
                mdfc = [c for c in pl.hand if c.is_mdfc_land]
                if mdfc and not any(self.castable_now(pl, c) for c in pl.hand if not c.is_land):
                    lands = mdfc
                else:
                    return
            have = set()
            for u in pl.pool:
                have |= u
            for _, units in pl.lands:
                for u in units:
                    have |= u
            needed = set()
            for c in pl.hand + pl.command_zone:
                needed |= c.cost.colors

            def score(card):
                units = card.params.get("mana") or card.params.get("back_mana") or []
                cols = set().union(*units) if units else set()
                s = 3 * len((cols & needed) - have) + 0.5 * len(cols)
                if not self.enters_tapped(pl, card):
                    s += 1.5
                if card.is_mdfc_land:
                    s -= 2
                return s
            best = max(lands, key=score)
            pl.hand.remove(best)
            self.put_land(pl, best, tapped=self.enters_tapped(pl, best))
            pl.land_drops -= 1

    @staticmethod
    def enters_tapped(pl: Player, card: Card) -> bool:
        P = card.params
        if P.get("enters_tapped"):
            return True
        if "tapped_unless_lands" in P:
            return len(pl.lands) < P["tapped_unless_lands"]
        return False

    def put_land(self, pl: Player, card: Card, tapped: bool) -> None:
        units = card.params.get("mana") if card.is_land else card.params.get("back_mana")
        if units is None:
            units = [frozenset(_COLOR_OF_BASIC.get(card.name, "C"))]
        pl.lands.append([card, units])
        if not tapped:
            pl.pool.extend(units)
        # landfall proliferate (Evolution Sage)
        for pc in pl.all_permanent_cards():
            for pr in pc.params.get("proliferate", []):
                if pr["timing"] == "on_land":
                    self.proliferate(pl)

    def search_lands(self, pl: Player, ls: dict) -> None:
        allowed = set(ls.get("colors") or [])
        need = set(pl.colors)
        have = set()
        for _, units in pl.lands:
            for u in units:
                have |= u
        basics = [c for c in pl.library if c.is_basic and c.is_land and
                  (not allowed or _COLOR_OF_BASIC.get(c.name) in allowed)]
        if not basics:
            return

        def pref(c):
            col = _COLOR_OF_BASIC.get(c.name, "C")
            return (col in need and col not in have, col in need)
        picked = []
        for _ in range(ls["to_bf"] + ls["to_hand"]):
            pool = [c for c in basics if c not in picked]
            if not pool:
                break
            best = max(pool, key=pref)
            picked.append(best)
            have.add(_COLOR_OF_BASIC.get(best.name, "C"))
        for i, card in enumerate(picked):
            pl.library.remove(card)
            if i < ls["to_bf"]:
                self.put_land(pl, card, tapped=ls["tapped"])
            else:
                pl.hand.append(card)
        self.rng.shuffle(pl.library)

    # ---- casting ------------------------------------------------------------------------
    def cast_cost(self, pl: Player, card: Card, from_command=False) -> ManaCost:
        cost = card.cost
        if from_command:
            tax = 2 * pl.cmd_casts[card.name]
            if tax:
                cost = ManaCost(cost.generic + tax, cost.pips, cost.x_count)
        return cost

    def castable_now(self, pl: Player, card: Card, from_command=False, extra=0) -> bool:
        return pl.can_afford(self.cast_cost(pl, card, from_command), extra)

    def priority(self, pl: Player, card: Card, from_command=False):
        """How much the AI wants to cast this now (None = hold it)."""
        t, P = card.tags, card.params
        has_opps = bool(self.opponents(pl))
        engine = self.has_goad_engine(pl)
        if from_command:
            return 8.5
        if card.is_land:
            return None
        if P.get("fog") and not P.get("fog_turn"):
            return None
        if P.get("fog_turn"):
            if "suspend" in P:
                return None  # handled by suspend
            return 7 if has_opps and self.threat_to(pl) >= pl.life * 0.4 else None
        if "counterspell" in t and card.is_instant:
            return None
        removal = P.get("removal", [])
        if "ramp" in t and (card.is_permanent or card.is_sorcery):
            direct = "mana" in P or P.get("extra_land") or P.get("treasure") or any(
                ls["to_bf"] and ls["timing"] in ("etb", "sorcery", "static") for ls in P.get("land_search", []))
            if direct:
                # cheap ramp first; bigger ramp spells wait behind the commander
                return 10 + (2 - card.cmc) * 0.1 if card.cmc <= 2 else 8.2
        if "board_wipe" in t:
            return 7.5 if has_opps and self.wipe_value(pl, card)[0] >= 6 else None
        if card.is_instant:
            if engine and P.get("opp_counters") and self.best_counter_targets(pl, 1):
                return 6
            if removal and all(r["timing"] == "instant" for r in removal):
                tgt = self.best_removal_target(pl, card)
                if tgt and tgt[1] == "permanent" and tgt[0] >= 3:
                    return 7  # e.g. Disenchant on an opponent's Propaganda
                if tgt and tgt[1] == "creature" and tgt[0] >= 8:
                    return 6
                return None
            if "protection" in t:
                return None
            if "draw" in t or "proliferate" in t:
                return 3
            return None
        if card.is_sorcery:
            if removal and not P.get("draw_cast"):
                tgt = self.best_removal_target(pl, card)
                return 7 if tgt and tgt[0] >= 4 else None
            if P.get("opp_counters"):
                return 6.5 if has_opps and (engine or removal and self.best_removal_target(pl, card)) else None
            if "draw" in t or "proliferate" in t or "tutor" in t:
                return 5
            return 4
        # permanents
        if removal and removal[0]["timing"] in ("etb", "sorcery") and card.is_enchantment and not card.is_creature:
            tgt = self.best_removal_target(pl, card)
            if not tgt or tgt[0] < 4:
                return None
            return 7
        if card.params.get("copy_ench", {}).get("timing") == "etb":
            best = self.best_enchantment_to_copy(pl)
            return 7.5 if best is not None else None
        if t & PILLOW_TAGS or P.get("goad_on_counters"):
            return 8
        if card.is_creature:
            return 6 + min(card.power_int, 6) * 0.1
        if "draw" in t or "proliferate" in t:
            return 6.5
        return 5

    def main_phase(self, pl: Player, reserve: int = 0) -> None:
        for _ in range(30):
            options = []
            for card in pl.hand:
                if card.is_land:
                    continue
                if "suspend" in card.params:
                    cost = parse_mana_cost(card.params["suspend"]["cost"])
                    if self.opponents(pl) and pl.can_afford(cost, reserve):
                        options.append((7.2, card, "suspend"))
                    continue
                pr = self.priority(pl, card)
                if pr is None:
                    continue
                need = 0 if pr >= 9 else reserve
                if self.castable_now(pl, card, extra=need):
                    options.append((pr + card.cmc * 0.01, card, "hand"))
            for card in pl.command_zone:
                if self.castable_now(pl, card, from_command=True):
                    options.append((8.5, card, "command"))
            if not options:
                return
            options.sort(key=lambda o: o[0], reverse=True)
            _, card, zone = options[0]
            if zone == "suspend":
                pl.pay(parse_mana_cost(card.params["suspend"]["cost"]))
                pl.hand.remove(card)
                pl.suspended.append([card, card.params["suspend"]["turns"]])
                continue
            self.cast(pl, card, from_command=(zone == "command"))
            if self.winner is not None or not pl.alive:
                return

    def cast(self, pl: Player, card: Card, from_command=False, free=False) -> bool:
        cost = self.cast_cost(pl, card, from_command)
        if not free and not pl.pay(cost):
            return False
        if from_command:
            pl.command_zone.remove(card)
            pl.cmd_casts[card.name] += 1
            if pl.cmd_cast_turn is None:
                pl.cmd_cast_turn = pl.turn
        else:
            pl.hand.remove(card)
        pl.spells_cast += 1
        self.say(f"{pl.name} casts {card.name}" + (" (commander)" if from_command else ""))
        # spell cast triggers (Inexorable Tide...)
        for pc in pl.all_permanent_cards():
            for pr in pc.params.get("proliferate", []):
                if pr["timing"] == "on_cast":
                    self.proliferate(pl)
        if self.try_counter(pl, card):
            self.say(f"  ...countered")
            if from_command or card.name in pl.commander_names:
                pl.command_zone.append(card)
            else:
                pl.graveyard.append(card)
            return False
        self.resolve(pl, card, is_commander=from_command or card.name in pl.commander_names)
        return True

    def resolve(self, pl: Player, card: Card, is_commander=False, free=False) -> None:
        P, t = card.params, card.tags
        if card.is_permanent:
            if card.is_creature:
                c = Creature(card, pl.idx, card.power_int, card.toughness_int, is_commander=is_commander)
                if P.get("etb_counters"):
                    c.add_counter("+1/+1", P["etb_counters"])
                pl.creatures.append(c)
            elif "mana" in P:
                pl.rocks.append([card, P["mana"]])
                pl.pool.extend(P["mana"])
            elif "pump" in P:
                self.attach_pump(pl, card)
            elif P.get("copy_ench", {}).get("timing") == "etb":
                best = self.best_enchantment_to_copy(pl)
                pl.permanents.append(best.copy() if best is not None else card)
            else:
                pl.permanents.append(card)
        for ls in P.get("land_search", []):
            if ls["timing"] in ("sorcery", "instant", "etb", "static"):
                self.search_lands(pl, ls)
        if P.get("treasure"):
            pl.pool.extend([frozenset(pl.colors)] * P["treasure"])
        draw = P.get("draw_cast", 0) + P.get("draw_etb", 0)
        if draw:
            pl.draw_fraction(draw)
        for tok in P.get("tokens", []):
            if tok["timing"] in ("sorcery", "instant", "etb", "static"):
                self.make_tokens(pl, tok)
        for tut in P.get("tutor", []):
            self.tutor(pl, tut)
        for oc in P.get("opp_counters", []):
            if oc["timing"] in ("sorcery", "instant", "etb", "static"):
                self.place_opp_counters(pl, oc)
        for pr in P.get("proliferate", []):
            if pr["timing"] in ("sorcery", "instant"):
                self.proliferate(pl)
        removal = [r for r in P.get("removal", []) if r["timing"] in ("sorcery", "instant", "etb")]
        if removal and not self.goldfish:
            tgt = self.best_removal_target(pl, card)
            if tgt:
                self.apply_removal(pl, card, tgt)
        if P.get("wipe") and not self.goldfish:
            _, mode = self.wipe_value(pl, card)
            self.apply_wipe(pl, card, mode)
        if P.get("fog_turn"):
            pl.no_attacks_until_my_turn = True
        for d in P.get("drain", []):
            if d["timing"] in ("sorcery", "instant", "etb", "static"):
                self.drain(pl, d["amount"])
        if not card.is_permanent:
            if P.get("recur_every"):
                pl.suspended.append([card, P["recur_every"]])
            else:
                pl.graveyard.append(card)

    def instant_reserve(self, pl: Player) -> int:
        """Mana to keep open for counterspells, removal and fogs on other players' turns."""
        if self.goldfish:
            return 0
        held = [c for c in pl.hand if self.is_reactive(c)]
        if not held:
            return 0
        return min(c.cost.value() for c in held)

    @staticmethod
    def is_reactive(card: Card) -> bool:
        P = card.params
        return card.is_instant and ("counterspell" in card.tags or P.get("fog") or
                                    any(r["timing"] == "instant" for r in P.get("removal", [])))

    def held_value(self, pl: Player) -> int:
        """Mana left open this turn that could pay for interaction held in hand."""
        best = 0
        for c in pl.hand:
            if self.is_reactive(c) or "protection" in c.tags and c.is_instant:
                if pl.can_afford(c.cost):
                    best = max(best, c.cost.value())
        return min(best, len(pl.pool))

    def discard_to_hand_size(self, pl: Player) -> None:
        while len(pl.hand) > 7:
            lands_in_play = len(pl.lands)
            lands = [c for c in pl.hand if c.is_land]
            if lands and lands_in_play >= 5:
                victim = lands[0]
            else:
                victim = max(pl.hand, key=lambda c: (not c.is_land, c.cmc))
            pl.hand.remove(victim)
            pl.graveyard.append(victim)

    # ---- effects ------------------------------------------------------------------------
    def make_tokens(self, pl: Player, tok: dict) -> None:
        n = tok["n"]
        whole = int(n) + (1 if self.rng.random() < n - int(n) else 0)
        for _ in range(whole):
            pl.creatures.append(Creature(None, pl.idx, tok["power"], tok["toughness"], is_token=True,
                                         flying=tok.get("flying", False), vigilance=tok.get("vigilance", False)))

    def attach_pump(self, pl: Player, card: Card) -> None:
        pl.permanents.append(card)
        if not pl.creatures:
            return
        target = max(pl.creatures, key=lambda c: (c.is_commander, c.power()))
        if "equip" in card.params and not pl.pay_generic(card.params["equip"]):
            return
        p, t = card.params["pump"]
        target.bonus_p += p
        target.bonus_t += t
        if card.params.get("pump_flying"):
            target.flying = True

    def tutor(self, pl: Player, kind: str) -> None:
        pool = [c for c in pl.library if not c.is_land and (kind == "any" or getattr(c, f"is_{kind}", False))]
        if not pool:
            return
        best = max(pool, key=lambda c: (bool(c.tags & PILLOW_TAGS), c.cmc <= len(pl.lands) + 1, c.cmc))
        pl.library.remove(best)
        pl.hand.append(best)
        self.rng.shuffle(pl.library)

    def drain(self, pl: Player, amount: int) -> None:
        for q in self.opponents(pl):
            self.lose_life(q, amount)
        self.check_deaths()

    def lose_life(self, q: Player, n: int) -> None:
        if n > 0:
            q.life -= n
            q.damage_taken += n

    def has_goad_engine(self, pl: Player) -> bool:
        return any(c.card is not None and c.card.params.get("goad_on_counters") for c in pl.creatures) or \
            any(c.params.get("goad_on_counters") for c in pl.permanents)

    def best_counter_targets(self, pl: Player, n) -> list:
        cands = [c for q in self.opponents(pl) for c in q.creatures if not c.cant_attack]
        cands.sort(key=lambda c: self.threat_score(pl, c), reverse=True)
        if n == "all":
            return cands
        return cands[:n]

    def place_opp_counters(self, pl: Player, oc: dict) -> None:
        if self.goldfish:
            return
        n, kind = oc["n"], oc.get("kind", "counter")
        engine = self.has_goad_engine(pl)
        if oc.get("any_target") and not engine:
            # without Kros-style goad, these go on your own creatures instead
            if kind == "shield" and pl.creatures:
                best = max(pl.creatures, key=lambda c: c.power())
                best.add_counter("shield")
            return
        if n == "all_opps":
            targets = [c for q in self.opponents(pl) for c in q.creatures]
        elif n == "all_one":
            opps = self.opponents(pl)
            if not opps:
                return
            q = max(opps, key=lambda q: q.board_power())
            targets = list(q.creatures)
        elif n == "one_each_opp":
            targets = []
            for q in self.opponents(pl):
                if q.creatures:
                    targets.append(max(q.creatures, key=lambda c: self.threat_score(pl, c)))
        else:
            targets = self.best_counter_targets(pl, int(n))
        if not targets:
            return
        self.say(f"  {pl.name} puts {kind} counters on " + ", ".join(f"{c.name}({self.players[c.owner].name})"
                                                                 for c in targets))
        for c in targets:
            if kind == "vow":
                c.no_attack_vs.add(pl.idx)
            c.add_counter(kind)
            if oc.get("tap") and not c.tapped:
                self.tap_opp_creature(pl, c)
        self.on_counters_placed(pl, targets)

    def on_counters_placed(self, pl: Player, creatures: list) -> None:
        """Triggers for putting counters on creatures you don't control (Kros, Generous Patron)."""
        perms = pl.all_permanent_cards()
        goad = any(c.params.get("goad_on_counters") for c in perms)
        draws = sum(c.params.get("draw_on_opp_counters", 0) for c in perms)
        for c in creatures:
            if c.owner == pl.idx:
                continue
            if goad:
                if not c.tapped:
                    self.tap_opp_creature(pl, c)
                c.goaded_by.add(pl.idx)
                c.trample = True
        if draws:
            pl.draw(min(4, int(draws * len([c for c in creatures if c.owner != pl.idx]))))

    def tap_opp_creature(self, pl: Player, c: Creature) -> None:
        c.tapped = True
        owner = self.players[c.owner]
        # Palliation Accord-style triggers belong to the owner's opponents
        for q in self.opponents(owner):
            if q.has_param("accord"):
                q.accord_counters += 1
        perms = pl.all_permanent_cards()
        for pc in perms:
            P = pc.params
            if P.get("draw_on_opp_tap"):
                if P.get("draw_on_opp_tap_once"):
                    if not pl.drew_from_tap_this_turn:
                        pl.draw(1)
                        pl.drew_from_tap_this_turn = True
                else:
                    pl.draw_fraction(P["draw_on_opp_tap"])
            if P.get("hylda") and pl.pool:
                pl.pay_generic(1)
                pl.creatures.append(Creature(None, pl.idx, 4, 4, is_token=True))

    def proliferate(self, pl: Player) -> None:
        if self.goldfish:
            for c in pl.creatures:
                if c.counters["+1/+1"]:
                    c.add_counter("+1/+1")
            return
        affected = []
        for q in self.opponents(pl):
            for c in q.creatures:
                if c.total_counters() > 0:
                    kind = max((k for k, v in c.counters.items() if v > 0 and k != "+1/+1"),
                               key=lambda k: c.counters[k], default="+1/+1")
                    c.add_counter(kind)
                    affected.append(c)
        for c in pl.creatures:
            if c.counters["+1/+1"]:
                c.add_counter("+1/+1")
        if affected:
            self.on_counters_placed(pl, affected)

    # ---- removal / wipes ----------------------------------------------------------------
    def threat_score(self, pl: Player, c: Creature) -> float:
        owner = self.players[c.owner]
        p = c.power(owner.anthem())
        s = p + (3 if c.is_commander else 0) + (2 if c.flying or c.unblockable else 0)
        if c.infect:
            s += p * 2
        if owner.idx in pl.attacked_by_last_round or owner.idx in pl.attacked_by_this_round:
            s += 2
        if pl.idx in c.goaded_by:
            s -= 3  # already pointed at someone else
        if c.cant_attack:
            s -= p
        return s

    def best_removal_target(self, pl: Player, card: Card):
        """(score, kind, target) for the best thing this removal could hit, or None."""
        best = None
        for r in card.params.get("removal", []):
            types = set(r.get("types", []))
            if "creature" in types:
                for q in self.opponents(pl):
                    for c in q.creatures:
                        if r.get("min_toughness") and c.toughness(q.anthem()) < r["min_toughness"]:
                            continue
                        if r["kind"] == "edict" and r.get("flying_only") and not c.flying:
                            continue
                        s = self.threat_score(pl, c)
                        if best is None or s > best[0]:
                            best = (s, "creature", c, r)
            nc_types = types & {"artifact", "enchantment", "planeswalker"}
            if nc_types:
                for q in self.opponents(pl):
                    for perm in q.permanents + [rk[0] for rk in q.rocks]:
                        if not any(getattr(perm, f"is_{t}") for t in nc_types):
                            continue
                        s = self.permanent_threat(pl, q, perm)
                        if best is None or s > best[0]:
                            best = (s, "permanent", (q, perm), r)
        return best

    def permanent_threat(self, pl: Player, owner: Player, perm: Card) -> float:
        """How much an opponent's noncreature permanent is in this player's way."""
        P = perm.params
        attackers = len([c for c in pl.creatures if not c.cant_attack])
        s = 0.0
        for tax in P.get("attack_tax", []):
            amt = tax.get("amount") or (owner.enchantment_count() if tax.get("per") == "enchantments" else 1)
            s += amt * attackers * 0.8
        if P.get("prevent_all") or P.get("no_attacks_you") or P.get("delay_shield"):
            s += 2 + attackers
        if "anthem" in P:
            s += len(owner.creatures)
        if "mana" in P:
            s += 1.5
        if perm.tags & {"draw", "token_maker"}:
            s += 2
        return s

    def apply_removal(self, pl: Player, card: Card, tgt) -> None:
        _, kind, target, r = tgt
        if kind == "creature":
            owner = self.players[target.owner]
            if r["kind"] == "edict":
                victims = [c for c in owner.creatures if (not r.get("flying_only") or c.flying)]
                if victims:
                    self.remove_creature(min(victims, key=lambda c: c.power()))
                return
            if self.try_protect(owner, target):
                return
            if r["kind"] in ("destroy", "damage", "minus") and target.counters["shield"] > 0:
                target.counters["shield"] -= 1
                return
            if r["kind"] == "exile" and card.name == "Swords to Plowshares":
                owner.life += target.power(owner.anthem())
            self.remove_creature(target)
        else:
            q, perm = target
            if perm in q.permanents:
                q.permanents.remove(perm)
            else:
                q.rocks = [rk for rk in q.rocks if rk[0] is not perm]
            if not perm.synthetic:
                q.graveyard.append(perm)

    def remove_creature(self, c: Creature) -> None:
        owner = self.players[c.owner]
        self.say(f"  {c.name} ({owner.name}) leaves the battlefield")
        if c in owner.creatures:
            owner.creatures.remove(c)
        if c.card is not None and not c.is_token:
            if c.is_commander:
                owner.command_zone.append(c.card)
            else:
                owner.graveyard.append(c.card)

    def try_protect(self, owner: Player, target: Creature) -> bool:
        """The owner answers removal with a protection instant if it's worth it."""
        if self.goldfish or self.threat_score(owner, target) < 4 and not target.is_commander:
            return False
        for c in owner.hand:
            if c.is_instant and "protection" in c.tags and owner.can_afford(c.cost):
                owner.pay(c.cost)
                owner.hand.remove(c)
                owner.graveyard.append(c)
                return True
        return False

    def wipe_value(self, pl: Player, card: Card) -> tuple:
        """(net value, mode) of casting a board wipe: what opponents lose minus what you lose."""
        best = (-99, None)
        for mode in card.params.get("wipe", []):
            mine = theirs = 0.0
            for q in self.players:
                if not q.alive:
                    continue
                a = q.anthem()
                for c in q.creatures:
                    hit = True
                    if mode.get("state") == "tapped":
                        hit = c.tapped
                    elif mode.get("state") == "untapped":
                        hit = not c.tapped
                    if mode.get("one_sided") and q is pl:
                        hit = False
                    v = c.power(a) + (3 if c.is_commander else 0) + 1
                    if mode["kind"] == "keep_one":
                        hit = c is not max(q.creatures, key=lambda x: x.power(a))
                    if hit:
                        if q is pl:
                            mine += v
                        else:
                            theirs += v * (1.5 if q.idx in pl.attacked_by_last_round else 1.0)
            val = theirs - 1.5 * mine
            if val > best[0]:
                best = (val, mode)
        return best

    def apply_wipe(self, pl: Player, card: Card, mode) -> None:
        if not mode:
            return
        for q in self.players:
            if not q.alive or not q.creatures:
                continue
            a = q.anthem()
            keep = max(q.creatures, key=lambda x: x.power(a)) if mode["kind"] == "keep_one" else None
            for c in list(q.creatures):
                if mode.get("one_sided") and q is pl:
                    continue
                if mode.get("state") == "tapped" and not c.tapped:
                    continue
                if mode.get("state") == "untapped" and c.tapped:
                    continue
                if c is keep:
                    if mode.get("kept_cant_attack_you"):
                        c.no_attack_vs.add(pl.idx)
                    continue
                if mode["kind"] == "destroy" and c.counters["shield"] > 0:
                    c.counters["shield"] -= 1
                    continue
                self.remove_creature(c)

    # ---- counterspells ------------------------------------------------------------------
    def spell_impact(self, defender: Player, caster: Player, card: Card) -> float:
        """How badly ``defender`` wants ``card`` (cast by ``caster``) to not resolve."""
        P, t = card.params, card.tags
        s = 0.0
        if card.is_creature:
            s += card.power_int * (1.5 if "threat" in t else 1.0) - 2
        if "board_wipe" in t:
            val = 0.0
            for c in defender.creatures:
                val += c.power() + 1
            s += val - 2
        if P.get("removal"):
            tgt = self.best_removal_target(caster, card)
            if tgt and tgt[1] == "permanent" and tgt[2][0] is defender:
                s += 3 + self.permanent_threat(caster, defender, tgt[2][1])
            elif tgt and tgt[1] == "creature" and tgt[2].owner == defender.idx:
                s += 2 + tgt[2].power()
        if "anthem" in P:
            s += len(caster.creatures) * 0.8
        if t & {"wincon"}:
            s += 4
        for combo in self.combos:
            if card.name in combo["cards"] and self.combo_ready(caster, combo, extra=card):
                s += 50
        return s

    def try_counter(self, caster: Player, card: Card) -> bool:
        if self.goldfish:
            return False
        for q in self.opponents(caster):
            for c in q.hand:
                if c.is_instant and "counterspell" in c.tags and q.can_afford(c.cost):
                    kinds = c.params.get("counter_types", "any")
                    if "noncreature" in kinds and card.is_creature:
                        continue
                    if kinds.startswith("creature") and not card.is_creature:
                        continue
                    if self.spell_impact(q, caster, card) >= 6:
                        q.pay(c.cost)
                        q.hand.remove(c)
                        q.graveyard.append(c)
                        return True
        return False

    # ---- activated abilities ------------------------------------------------------------
    def use_activated(self, pl: Player) -> None:
        if not pl.pool:
            return
        reserve = self.instant_reserve(pl)
        sources = [c.card for c in pl.creatures if c.card is not None and not c.sick and not c.tapped] + \
            pl.permanents + [r[0] for r in pl.rocks] + [ld[0] for ld in pl.lands]
        engine = self.has_goad_engine(pl)
        for card in sources:
            P = card.params
            key = id(card)
            if key in pl.used_this_turn:
                continue
            # copy an enchantment (Yenna)
            ce = P.get("copy_ench")
            if ce and ce["timing"] == "activated":
                best = self.best_enchantment_to_copy(pl)
                cost = parse_mana_cost(ce["cost"])
                if best is not None and pl.can_afford(cost, reserve):
                    pl.pay(cost)
                    pl.permanents.append(best.copy())
                    pl.used_this_turn.add(key)
                    continue
            for oc in P.get("opp_counters", []):
                if oc["timing"] == "activated" and not self.goldfish:
                    cost = parse_mana_cost(oc["cost"])
                    targets = self.best_counter_targets(pl, 1)
                    if targets and self.threat_score(pl, targets[0]) >= 3 and pl.can_afford(cost, reserve):
                        pl.pay(cost)
                        pl.used_this_turn.add(key)
                        self.place_opp_counters(pl, dict(oc, n=1))
            for pr in P.get("proliferate", []):
                if pr["timing"] == "activated" and key not in pl.used_this_turn:
                    worth = (engine and any(c.total_counters() for q in self.opponents(pl) for c in q.creatures)) \
                        or sum(c.counters["+1/+1"] for c in pl.creatures) >= 3
                    cost = parse_mana_cost(pr["cost"])
                    if worth and pl.can_afford(cost, reserve):
                        pl.pay(cost)
                        pl.used_this_turn.add(key)
                        self.proliferate(pl)
            for ls in P.get("land_search", []):
                if ls["timing"] == "activated" and key not in pl.used_this_turn:
                    if pl.ability_uses[key] >= ls.get("uses", 99):
                        continue
                    cost = parse_mana_cost(ls["cost"])
                    if pl.can_afford(cost, reserve) and len(pl.lands) < 9:
                        pl.pay(cost)
                        pl.used_this_turn.add(key)
                        pl.ability_uses[key] += 1
                        self.search_lands(pl, ls)

    def best_enchantment_to_copy(self, pl: Player):
        cands = [c for c in pl.permanents if c.is_enchantment and (c.tags & PILLOW_TAGS or "draw" in c.tags)]
        if not cands:
            return None
        return max(cands, key=lambda c: (len(c.tags & PILLOW_TAGS), c.cmc))

    # ---- combat -------------------------------------------------------------------------
    def can_attack(self, c: Creature) -> bool:
        return not (c.tapped or c.sick or c.cant_attack or c.defender) and c.power() > 0

    def defender_info(self, d: Player) -> dict:
        """Everything about a defending player that attack decisions need, computed once."""
        perms = d.all_permanent_cards()
        info = {"blocked": False, "moat": False, "taxes": [], "cap": 99, "prevent_all": False, "delay": False,
                "enchantments": d.enchantment_count(), "anthem": d.anthem()}
        for perm in perms:
            P = perm.params
            if P.get("no_attacks_you") or P.get("no_attacks_global"):
                info["blocked"] = True
            if P.get("moat"):
                info["moat"] = True
            info["taxes"].extend(P.get("attack_tax", ()))
            if "max_attackers_you" in P:
                info["cap"] = min(info["cap"], P["max_attackers_you"])
            if P.get("prevent_all"):
                info["prevent_all"] = True
            if P.get("delay_shield"):
                info["delay"] = True
        if d.no_attacks_until_my_turn:
            info["blocked"] = True
        info["blockers"] = [b for b in d.creatures if not b.tapped and not b.cant_block]
        return info

    def attack_tax(self, d: Player, c: Creature, info: dict | None = None):
        """Mana the attacker must pay for ``c`` to attack ``d``; None if it can't attack ``d``."""
        info = info or self.defender_info(d)
        if info["blocked"] or d.idx in c.no_attack_vs or (info["moat"] and not c.flying):
            return None
        tax = 0
        for t in info["taxes"]:
            if t.get("only_with_counters") and c.total_counters() == 0:
                continue
            if t.get("per") == "enchantments":
                tax += info["enchantments"]
            elif t.get("per") == "counters":
                tax += c.total_counters()
            else:
                tax += t["amount"]
        return tax

    def threat_to(self, pl: Player) -> int:
        return sum(q.board_power() for q in self.opponents(pl))

    def plan_attack_reserve(self, pl: Player) -> int:
        """Mana to hold back in main phase 1 to pay attack taxes worth paying."""
        plan, tax = self.plan_attacks(pl, budget=len(pl.pool), dry_run=True)
        return tax

    def attack_value(self, a: Player, c: Creature, d: Player, assigned: int, info: dict, anthem, leader) -> float:
        p = c.power(anthem)
        if p <= 0 or info["prevent_all"]:
            return -1
        dmg = float(p)
        if info["delay"]:
            dmg *= 0.5
        da = info["anthem"]
        ct = c.toughness(anthem)
        kills_me = walls = False
        if not c.unblockable:
            for b in info["blockers"]:
                if c.flying and not b.flying:
                    continue
                if b.power(da) >= ct and c.counters["shield"] == 0:
                    kills_me = True
                if b.toughness(da) > p or b.counters["shield"]:
                    walls = True
        if walls and not c.trample:
            dmg *= 0.35
        if c.infect:
            dmg *= 4
        val = dmg
        if d.life - assigned - dmg <= 0:
            val += 15
        if c.is_commander and c.card is not None and d.cmd_damage[cmd_key(c)] + p >= 21:
            val += 15
        if d.idx in a.attacked_by_last_round:
            val *= 1.3
        if leader is d:
            val *= 1.15
        if kills_me:
            val -= 0.6 * (p + 2)
        return val * self.rng.uniform(0.85, 1.15)

    def plan_attacks(self, a: Player, budget: int, dry_run=False):
        opps = self.opponents(a)
        if not opps:
            return [], 0
        attackers = [c for c in a.creatures if self.can_attack(c)]
        if not attackers:
            return [], 0
        attackers.sort(key=lambda c: (not c.goaded_by, -c.power()))
        infos = {d.idx: self.defender_info(d) for d in opps}
        anthem = a.anthem()
        leader = max(opps, key=lambda q: q.board_power() + q.life * 0.1)
        threatened = self.threat_to(a) >= a.life * 0.6
        if threatened and not dry_run:
            home = [c for c in attackers if not c.goaded_by and not c.vigilance]
            if home:
                keep = max(home, key=lambda c: c.toughness())
                attackers.remove(keep)
        plan, total_tax = [], 0
        assigned = defaultdict(int)
        counts = defaultdict(int)
        for c in attackers:
            must = bool(c.goaded_by)
            allowed = []
            for d in opps:
                info = infos[d.idx]
                tax = self.attack_tax(d, c, info)
                if tax is None or tax > budget - total_tax or counts[d.idx] >= info["cap"]:
                    continue
                allowed.append((d, tax))
            if must:
                non_goader = [(d, t) for d, t in allowed if d.idx not in c.goaded_by]
                if non_goader:
                    allowed = non_goader
            best, best_val = None, (-1e9 if must else 0.5)
            for d, tax in allowed:
                val = self.attack_value(a, c, d, assigned[d.idx], infos[d.idx], anthem, leader) - tax * TAX_VALUE
                if must and tax == 0:
                    val += 100  # goaded: it has to attack if it can do so for free
                if val > best_val:
                    best, best_val = (d, tax), val
            if best is None:
                continue
            d, tax = best
            plan.append((c, d))
            total_tax += tax
            assigned[d.idx] += c.power(anthem)
            counts[d.idx] += 1
        return plan, total_tax

    def combat(self, a: Player) -> None:
        # defenders may tap down a big attacker first (Verity Circle style)
        self.precombat_taps(a)
        plan, tax = self.plan_attacks(a, budget=len(a.pool))
        if not plan:
            return
        a.pay_generic(tax)
        if self.verbose:
            per = defaultdict(list)
            for c, d in plan:
                per[d.name].append(f"{c.name} {c.power(a.anthem())}" + (" [goaded]" if c.goaded_by else ""))
            for dn, lst in per.items():
                self.say(f"{a.name} attacks {dn} with " + ", ".join(lst))
            if tax:
                self.say(f"  {a.name} pays {tax} attack tax")
        by_def = defaultdict(list)
        for c, d in plan:
            if not c.vigilance:
                c.tapped = True
            by_def[d.idx].append(c)
            d.tax_collected += self.attack_tax(d, c) or 0
            d.attacked_by_this_round.add(a.idx)
        n_tapped = sum(1 for c, _ in plan if not c.vigilance)
        for q in self.opponents(a):
            if q.has_param("accord"):
                q.accord_counters += n_tapped
        fogged = False
        for d_idx, atks in by_def.items():
            d = self.players[d_idx]
            d.times_attacked += 1
            d.attackers_faced += len(atks)
            anthem = a.anthem()
            # Lulu-style: stun an attacker
            for perm in d.all_permanent_cards():
                if perm.params.get("stun_attacker") and atks:
                    big = max(atks, key=lambda c: c.power(anthem))
                    big.add_counter("stun")
                    self.on_counters_placed(d, [big])
            incoming = sum(c.power(anthem) for c in atks)
            # instant-speed removal on the biggest attacker
            for card in list(d.hand):
                if not atks:
                    break
                rem = [r for r in card.params.get("removal", []) if r["timing"] == "instant" and
                       "creature" in r.get("types", [])]
                if rem and card.is_instant and d.can_afford(card.cost):
                    big = max(atks, key=lambda c: c.power(anthem))
                    if rem[0].get("min_toughness") and big.toughness(anthem) < rem[0]["min_toughness"]:
                        continue
                    if big.power(anthem) >= 4 or incoming >= d.life:
                        d.pay(card.cost)
                        d.hand.remove(card)
                        d.graveyard.append(card)
                        if self.try_counter(d, card):
                            continue
                        if big.counters["shield"] > 0 and rem[0]["kind"] != "exile":
                            big.counters["shield"] -= 1
                        else:
                            atks.remove(big)
                            if big in a.creatures:
                                self.remove_creature(big)
                        incoming = sum(c.power(anthem) for c in atks)
            # fog
            if not fogged and (incoming >= max(8, d.life * 0.35) or incoming >= d.life):
                for card in list(d.hand):
                    if card.params.get("fog") and not card.params.get("fog_turn") and d.can_afford(card.cost):
                        d.pay(card.cost)
                        d.hand.remove(card)
                        d.graveyard.append(card)
                        fogged = True
                        break
        if fogged:
            self.say("  fog: no combat damage")
            for d_idx, atks in by_def.items():
                self.players[d_idx].damage_prevented += sum(c.power(a.anthem()) for c in atks)
            return
        for d_idx, atks in by_def.items():
            self.resolve_combat(a, self.players[d_idx], atks)
        self.check_deaths()

    def precombat_taps(self, a: Player) -> None:
        for q in self.opponents(a):
            for perm in q.all_permanent_cards():
                for tp in perm.params.get("tap_opp", []):
                    if tp["timing"] != "activated" or id(perm) in q.used_this_turn:
                        continue
                    cands = [c for c in a.creatures if self.can_attack(c) and (tp["flying_ok"] or not c.flying)
                             and q.idx not in c.goaded_by and self.attack_tax(q, c) is not None]
                    if not cands:
                        continue
                    big = max(cands, key=lambda c: c.power())
                    cost = parse_mana_cost(tp["cost"])
                    if big.power() >= 3 and q.can_afford(cost):
                        q.pay(cost)
                        q.used_this_turn.add(id(perm))
                        self.tap_opp_creature(q, big)

    def resolve_combat(self, a: Player, d: Player, atks: list) -> None:
        aa, da = a.anthem(), d.anthem()
        blockers = [b for b in d.creatures if not b.tapped and not b.cant_block]
        unblocked = []
        incoming = sum(c.power(aa) for c in atks)
        for c in sorted(atks, key=lambda x: -x.power(aa)):
            p = c.power(aa)
            cands = [b for b in blockers if (not c.flying or b.flying) and not c.unblockable]
            if c.menace and len(cands) < 2:
                cands = []
            good = [b for b in cands if b.toughness(da) > p or b.counters["shield"]]
            trades = [b for b in cands if b.power(da) >= c.toughness(aa) and not c.counters["shield"]
                      and not b.is_commander and b.power(da) + b.toughness(da) <= c.power(aa) + c.toughness(aa)]
            chumps = [b for b in cands if not b.is_commander and (b.is_token or b.power(da) <= 1)]
            pick = None
            if good:
                pick = min(good, key=lambda b: (b.power(da) < c.toughness(aa), b.toughness(da)))
            elif trades:
                pick = min(trades, key=lambda b: b.power(da) + b.toughness(da))
            elif cands and (incoming >= d.life or p >= 6 and d.life <= 15):
                pick = min(cands, key=lambda b: (b.is_commander, b.power(da) + b.toughness(da)))
            elif chumps and incoming >= d.life * 0.5:
                pick = min(chumps, key=lambda b: b.power(da) + b.toughness(da))
            if pick is None:
                unblocked.append(c)
                continue
            blockers.remove(pick)
            incoming -= p
            # fight
            self.deal_creature_damage(pick, p, d)
            self.deal_creature_damage(c, pick.power(da), a)
            if c.trample:
                excess = p - max(0, pick.toughness(da))
                if excess > 0:
                    unblocked.append((c, excess))
        total = 0
        damaged_by = []
        for item in unblocked:
            c, amount = (item if isinstance(item, tuple) else (item, item.power(aa)))
            if amount <= 0:
                continue
            dealt = self.damage_player(d, amount, c)
            if dealt:
                damaged_by.append(c)
                total += dealt
                if c.is_commander:
                    d.cmd_damage[cmd_key(c)] += dealt
                for pr in (c.card.params.get("proliferate", []) if c.card else []):
                    if pr["timing"] == "on_combat_damage":
                        self.proliferate(a)
        if total:
            self.say(f"  {d.name} takes {total} (life {d.life}, delayed {d.delay_counters})")
        for perm in d.all_permanent_cards():
            if perm.params.get("aurification"):
                for c in damaged_by:
                    c.add_counter("gold")
                    c.cant_attack = True

    def deal_creature_damage(self, c: Creature, amount: int, owner: Player) -> None:
        if amount <= 0:
            return
        if c.counters["shield"] > 0:
            c.counters["shield"] -= 1
            return
        c.damage += amount
        if c.damage >= c.toughness(owner.anthem()):
            self.remove_creature(c)

    def damage_player(self, d: Player, amount: int, source: Creature | None = None) -> int:
        perms = d.all_permanent_cards()
        if any(p.params.get("prevent_all") for p in perms):
            d.damage_prevented += amount
            return 0
        for p in perms:
            if p.params.get("prevent_each"):
                cut = min(amount, p.params["prevent_each"])
                amount -= cut
                d.damage_prevented += cut
        if d.accord_counters and amount > 0:
            cut = min(amount, d.accord_counters)
            d.accord_counters -= cut
            amount -= cut
            d.damage_prevented += cut
        if amount <= 0:
            return 0
        if any(p.params.get("delay_shield") for p in perms):
            d.delay_counters += amount
            return 0
        if source is not None and source.infect:
            d.poison += amount
            d.damage_taken += amount
            return amount
        d.life -= amount
        d.damage_taken += amount
        return amount

    # ---- combos -------------------------------------------------------------------------
    def combo_ready(self, pl: Player, combo: dict, extra: Card | None = None) -> bool:
        have = {c.name for c in pl.all_permanent_cards()} | {c.name.split(" // ")[0] for c in
                                                             pl.all_permanent_cards()}
        if extra is not None:
            have |= {extra.name, extra.name.split(" // ")[0]}
        return all(name in have for name in combo["cards"])

    def check_combo_win(self, pl: Player) -> None:
        if self.goldfish or not self.combos:
            return
        for combo in self.combos:
            if combo.get("wins") and self.combo_ready(pl, combo):
                pl_left = len(pl.pool)
                if pl_left >= combo.get("mana", 0):
                    self.winner = pl.idx
                    for q in self.opponents(pl):
                        q.alive = False
                        q.died_round = self.round
                    return
