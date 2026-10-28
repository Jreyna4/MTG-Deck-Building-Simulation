"""The Card model, built from Scryfall card data."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import cached_property

from .mana import ManaCost, parse_mana_cost

_DFC_LAYOUTS = {"modal_dfc", "transform", "flip", "meld", "reversible_card"}


def _to_int(value) -> int:
    if value is None:
        return 0
    m = re.match(r"-?\d+", str(value))
    return int(m.group()) if m else 0


@dataclass(eq=False)
class Card:
    name: str
    mana_cost: str = ""
    cmc: float = 0
    type_line: str = ""
    oracle_text: str = ""
    colors: list = field(default_factory=list)
    color_identity: list = field(default_factory=list)
    produced_mana: list = field(default_factory=list)
    power: str | None = None
    toughness: str | None = None
    keywords: list = field(default_factory=list)
    layout: str = "normal"
    faces: list = field(default_factory=list)
    image_url: str | None = None
    scryfall_uri: str | None = None
    price_usd: str | None = None
    synthetic: bool = False  # made-up card used by the built-in practice opponents
    # filled in by the tagger
    tags: set = field(default_factory=set)
    params: dict = field(default_factory=dict)

    # ---- construction ---------------------------------------------------------------
    @classmethod
    def from_scryfall(cls, data: dict) -> "Card":
        faces = data.get("card_faces") or []
        front = faces[0] if faces else {}
        mana_cost = data.get("mana_cost") or front.get("mana_cost") or ""
        mana_cost = mana_cost.split(" // ")[0]
        oracle = data.get("oracle_text")
        if not oracle and faces:
            oracle = "\n//\n".join(f.get("oracle_text", "") for f in faces)
        image = (data.get("image_uris") or front.get("image_uris") or {}).get("normal")
        return cls(
            name=data.get("name", "?"),
            mana_cost=mana_cost,
            cmc=float(data.get("cmc") or 0),
            type_line=data.get("type_line", ""),
            oracle_text=oracle or "",
            colors=list(data.get("colors") or front.get("colors") or []),
            color_identity=list(data.get("color_identity") or []),
            produced_mana=list(data.get("produced_mana") or []),
            power=data.get("power", front.get("power")),
            toughness=data.get("toughness", front.get("toughness")),
            keywords=list(data.get("keywords") or []),
            layout=data.get("layout", "normal"),
            faces=[{k: f.get(k) for k in ("name", "mana_cost", "type_line", "oracle_text", "power", "toughness")}
                   for f in faces],
            image_url=image,
            scryfall_uri=data.get("scryfall_uri"),
            price_usd=(data.get("prices") or {}).get("usd"),
        )

    def copy(self) -> "Card":
        c = Card(**{k: getattr(self, k) for k in self.__dataclass_fields__})
        c.tags = set(self.tags)
        c.params = dict(self.params)
        return c

    # ---- faces & types --------------------------------------------------------------
    @property
    def front_type(self) -> str:
        return self.type_line.split(" // ")[0]

    @property
    def back_type(self) -> str:
        parts = self.type_line.split(" // ")
        return parts[1] if len(parts) > 1 else ""

    @property
    def text(self) -> str:
        """Rules text the card has when cast from hand (front face for double-faced cards)."""
        if self.faces and (self.layout in _DFC_LAYOUTS or self.layout == "adventure"):
            # Adventures: only the main card is modeled, so its adventure half isn't double counted
            return self.faces[0].get("oracle_text") or ""
        return self.oracle_text

    @property
    def back_text(self) -> str:
        if len(self.faces) > 1:
            return self.faces[1].get("oracle_text") or ""
        return ""

    @cached_property
    def _front_words(self) -> frozenset:
        return frozenset(re.findall(r"[A-Za-z]+", self.front_type))

    def _is(self, word: str, type_line: str | None = None) -> bool:
        if type_line is None:
            return word in self._front_words
        return re.search(rf"\b{word}\b", type_line) is not None

    @property
    def is_land(self) -> bool:
        return self._is("Land")

    @property
    def is_mdfc_land(self) -> bool:
        return self.layout == "modal_dfc" and self._is("Land", self.back_type) and not self.is_land

    @property
    def is_basic(self) -> bool:
        return self._is("Basic")

    @property
    def is_creature(self) -> bool:
        return self._is("Creature")

    @property
    def is_artifact(self) -> bool:
        return self._is("Artifact")

    @property
    def is_enchantment(self) -> bool:
        return self._is("Enchantment")

    @property
    def is_instant(self) -> bool:
        return self._is("Instant")

    @property
    def is_sorcery(self) -> bool:
        return self._is("Sorcery")

    @property
    def is_planeswalker(self) -> bool:
        return self._is("Planeswalker")

    @property
    def is_legendary(self) -> bool:
        return self._is("Legendary")

    @property
    def can_be_commander(self) -> bool:
        return (self.is_legendary and self.is_creature) or "can be your commander" in self.oracle_text.lower()

    @property
    def is_permanent(self) -> bool:
        return not (self.is_instant or self.is_sorcery)

    @cached_property
    def cost(self) -> ManaCost:
        return parse_mana_cost(self.mana_cost)

    @property
    def mana_value(self) -> int:
        return int(self.cmc)

    @property
    def power_int(self) -> int:
        return _to_int(self.power)

    @property
    def toughness_int(self) -> int:
        return _to_int(self.toughness)

    def has_keyword(self, kw: str) -> bool:
        kw = kw.lower()
        return any(k.lower() == kw for k in self.keywords) or re.search(
            rf"(^|\n){kw}\b", self.text.lower()) is not None

    def __repr__(self) -> str:
        return f"Card({self.name!r})"
