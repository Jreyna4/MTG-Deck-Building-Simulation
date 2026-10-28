"""Simple web app for the simulator.

Run it with:   streamlit run app.py
"""
from __future__ import annotations

import pandas as pd
import streamlit as st

from mtgsim import report
from mtgsim.archetypes import ARCHETYPES, archetype
from mtgsim.deck import parse_deck
from mtgsim.loader import build_loaded_deck
from mtgsim.scryfall import CardCache, ScryfallError
from mtgsim.tagger import DEFENSE_TAGS, ROLE_GROUPS, defense_rating

st.set_page_config(page_title="MTG Deck Simulator", page_icon="🃏", layout="wide")


@st.cache_resource(show_spinner="Looking up cards on Scryfall...")
def load_deck(text: str, commander: str, name: str):
    deck = parse_deck(text, name=name or "My deck", commander=commander or None)
    return build_loaded_deck(deck, cache=CardCache())


@st.cache_resource(show_spinner="Training the mulligan model on your deck...")
def get_advisor(_deck, key: str):
    from mtgsim.mulligan import MulliganAdvisor
    return MulliganAdvisor(_deck)


def pct(x):
    return f"{100 * x:.1f}%"


# ---- sidebar: deck input -----------------------------------------------------------------
st.sidebar.title("Your deck")
uploaded = st.sidebar.file_uploader("Deck list file (.txt)", type=["txt", "dek", "dck"])
default_text = uploaded.read().decode("utf-8", errors="ignore") if uploaded else ""
text = st.sidebar.text_area("...or paste a deck list", value=default_text, height=260,
                            help="Moxfield, Archidekt or Arena text export. Mark the commander with a "
                                 "'COMMANDER:' line, or type its name below.")
commander = st.sidebar.text_input("Commander (if not marked in the list)")
deck_name = st.sidebar.text_input("Deck name", value="My deck")

st.title("MTG Deck Building Simulation")
if not text.strip():
    st.info("Paste a Commander deck list in the sidebar to start.")
    st.stop()

try:
    deck = load_deck(text, commander, deck_name)
except ScryfallError as e:
    st.error(str(e))
    st.stop()

deck_key = f"{deck_name}|{commander}|{hash(text)}"
if deck.not_found:
    st.warning("Not found on Scryfall (check spelling): " + ", ".join(deck.not_found))
for w in deck.warnings:
    st.caption(w)

tab_deck, tab_hands, tab_mull, tab_combos, tab_match = st.tabs(
    ["Deck & roles", "Opening hands", "Mulligan advisor", "Combos", "Matchup"])

# ---- deck ---------------------------------------------------------------------------------
with tab_deck:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Commander", ", ".join(c.name for c in deck.commanders) or "none")
    c2.metric("Colors", "".join(sorted(deck.colors)))
    c3.metric("Cards", len(deck.cards) + len(deck.commanders))
    c4.metric("Lands", deck.land_count)
    roles = []
    for title, tagset in ROLE_GROUPS:
        cards = [c.name for c, _ in deck.unique() if c.tags & tagset]
        count = sum(n for c, n in deck.unique() if c.tags & tagset)
        if count:
            roles.append({"Role": title, "Cards": count, "Which": ", ".join(cards) if title != "Lands" else ""})
    st.subheader("Roles")
    st.dataframe(pd.DataFrame(roles), hide_index=True, width="stretch")
    st.subheader("Every card")
    rows = []
    for c in deck.commanders:
        rows.append({"Card": c.name + " (commander)", "Copies": 1, "Mana value": int(c.cmc),
                     "Tags": ", ".join(sorted(c.tags - {"creature"})), "Defense (estimate)": defense_rating(c)})
    for c, n in deck.unique():
        rows.append({"Card": c.name, "Copies": n, "Mana value": int(c.cmc),
                     "Tags": ", ".join(sorted(c.tags - {"creature", "land"})) or ("land" if c.is_land else ""),
                     "Defense (estimate)": defense_rating(c)})
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch", height=500)
    with st.expander("What the tags mean"):
        st.text(report.tag_legend())

# ---- opening hands ------------------------------------------------------------------------
with tab_hands:
    from mtgsim.goldfish import run_goldfish
    from mtgsim.handstats import opening_hand_stats
    games = st.slider("Goldfish games", 500, 10000, 3000, step=500)
    if st.button("Run opening-hand stats", type="primary"):
        with st.spinner("Playing goldfish games..."):
            st.session_state["hands"] = (opening_hand_stats(deck), run_goldfish(deck, games=games))
    if "hands" in st.session_state:
        stats, gold = st.session_state["hands"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Average lands in opening 7", f"{stats['avg_lands_in_7']:.2f}")
        c2.metric("Average goldfish score", f"{gold['avg_score']:.1f}",
                  help="Mana put to use in turns 1-5 (spent, or held up for instants)")
        ct = gold["commander_turn"]
        by4 = sum(v for k, v in ct.items() if 0 < k <= 4) / sum(ct.values())
        c3.metric("Commander cast by turn 4", pct(by4))
        left, right = st.columns(2)
        left.markdown("**Lands in opening 7**")
        left.bar_chart(pd.DataFrame({"chance": stats["land_distribution"]}))
        right.markdown("**Opening 7 has...**")
        right.dataframe(pd.DataFrame([{"": k, "Chance": pct(v)} for k, v in stats["hand_flags"].items()]),
                        hide_index=True, width="stretch")
        st.markdown("**Development by turn (goldfish)**")
        st.line_chart(pd.DataFrame(gold["turns"]).set_index("turn")[["lands", "mana", "used", "defense"]])
        st.markdown("**Chance to have at least one card of each role (on the draw)**")
        st.dataframe(pd.DataFrame([{"Role": r["role"], "Cards": r["count"], "Opening 7": pct(r["opening_7"]),
                                    "By turn 2": pct(r["turn_2"]), "By turn 4": pct(r["turn_4"]),
                                    "By turn 6": pct(r["turn_6"])} for r in stats["by_role"]]),
                     hide_index=True, width="stretch")
        spells = [c for c in gold["cards"] if not c["is_land"] and c["in_opener"] >= max(20, gold["games"] * 0.02)]
        a, b = st.columns(2)
        a.markdown("**Best cards to see in your opening hand**")
        a.dataframe(pd.DataFrame([{"Card": c["name"], "Score lift": round(c["lift"], 2),
                                   "Stuck in hand": pct(c["stuck_rate"])} for c in spells[:12]]),
                    hide_index=True, width="stretch")
        b.markdown("**Cards that slow your opening hand down**")
        b.dataframe(pd.DataFrame([{"Card": c["name"], "Score lift": round(c["lift"], 2),
                                   "Stuck in hand": pct(c["stuck_rate"])} for c in spells[-12:][::-1]]),
                    hide_index=True, width="stretch")
        st.caption("Removal, counterspells and fogs often look 'stuck' here because goldfish games have "
                   "no opponents to use them on.")

# ---- mulligan -----------------------------------------------------------------------------
with tab_mull:
    st.write("Pick the 7 cards you drew. The advisor plays this exact hand hundreds of times and compares "
             "it with what a mulligan is worth (your first mulligan in Commander is free).")
    names = sorted({c.name for c in deck.cards})
    cols = st.columns(7)
    picks = [cols[i].selectbox(f"Card {i + 1}", names, index=None, key=f"m{i}") for i in range(7)]
    mulls = st.number_input("Mulligans already taken", 0, 4, 0)
    if st.button("Keep or mulligan?", type="primary", disabled=None in picks):
        from mtgsim.mulligan import find_cards
        adv = get_advisor(deck, deck_key)
        try:
            hand = find_cards(deck, picks)
        except ValueError as e:
            st.error(str(e))
        else:
            with st.spinner("Playing this hand..."):
                r = adv.evaluate(hand, mulligans_taken=int(mulls), sims=400)
            (st.success if r["decision"] == "keep" else st.warning)(f"**{r['decision'].upper()}**")
            c1, c2, c3 = st.columns(3)
            c1.metric("Expected score (this hand)", f"{r['expected_score']:.1f}")
            if r["mulligan_value"] is not None:
                c2.metric(f"Expected score (mulligan to {r['cards_after_mulligan']})", f"{r['mulligan_value']:.1f}")
            c3.metric("Better than", f"{r['percentile']:.0f}% of random 7s")
            if r["bottom"]:
                st.write("Put on the bottom: " + ", ".join(r["bottom"]))
            for reason in r["reasons"]:
                st.write("- " + reason)

# ---- combos -------------------------------------------------------------------------------
with tab_combos:
    st.write("Finds combos in your deck with [Commander Spellbook](https://commanderspellbook.com).")
    if st.button("Find combos", type="primary"):
        from mtgsim.combos import ComboError, combos_for_deck
        try:
            with st.spinner("Asking Commander Spellbook..."):
                st.session_state["combos"] = combos_for_deck(deck)
        except ComboError as e:
            st.error(str(e))
    if "combos" in st.session_state:
        res = st.session_state["combos"]
        st.metric("Combos in your deck", len(res["included"]))
        for c in res["included"][:30]:
            with st.expander(" + ".join(c["cards"]) + (" (wins)" if c["wins"] else "")):
                st.write("**Result:** " + "; ".join(c["results"]))
                if c["prerequisites"]:
                    st.write("**Setup:** " + c["prerequisites"])
                st.write(c["steps"])
                st.markdown(f"[Open on Commander Spellbook]({c['url']})")
        if res["best_additions"]:
            st.subheader("Cards that would add the most combos")
            st.dataframe(pd.DataFrame(res["best_additions"]), hide_index=True, width="stretch")

# ---- matchup ------------------------------------------------------------------------------
with tab_match:
    from mtgsim.matchup import card_impact, run_matchup
    st.write("Simulated multiplayer games against practice decks or your friends' decks.")
    practice = st.multiselect("Practice opponents", list(ARCHETYPES), default=list(ARCHETYPES))
    pasted = []
    with st.expander("Add real opponent decks (paste lists)"):
        for i in range(3):
            t = st.text_area(f"Opponent deck {i + 1}", key=f"opp{i}", height=120)
            if t.strip():
                pasted.append(t)
    c1, c2 = st.columns(2)
    games = c1.slider("Games", 50, 1000, 300, step=50)
    impact = c2.selectbox("Also measure cards (slower)", ["no", "defense cards", "all cards"])
    if st.button("Run matchup", type="primary"):
        opps = [archetype(k) for k in practice]
        for i, t in enumerate(pasted):
            opps.append(load_deck(t, "", f"Opponent {i + 1}"))
        if not 1 <= len(opps) <= 3:
            st.error("Pick one to three opponents in total.")
        else:
            with st.spinner("Playing games..."):
                st.session_state["match"] = run_matchup(deck, opps, games=games)
            if impact != "no":
                want = DEFENSE_TAGS | {"goad", "counters_on_opponents", "proliferate", "copy_enchantment"}
                cards = None if impact == "all cards" else \
                    [c.name for c, _ in deck.unique() if not c.is_land and c.tags & want]
                bar = st.progress(0.0)
                st.session_state["impact"] = card_impact(
                    deck, opps, cards=cards, games=games,
                    progress=lambda i, n, name: bar.progress(i / n, text=f"Testing {name}"))
            else:
                st.session_state.pop("impact", None)
    if "match" in st.session_state:
        r = st.session_state["match"]
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Win rate", pct(r["win_rate"]), help=f"Fair share is {pct(r['fair_share'])}")
        c2.metric("Alive at the end", pct(r["survival_rate"]))
        c3.metric("Damage taken per game", f"{r['avg_damage_taken']:.1f}")
        c4.metric("Attack tax opponents paid", f"{r['avg_tax_collected']:.1f}")
        st.write("Game results: " + ", ".join(f"{k}: {v}" for k, v in r["outcomes"].items()))
    if "impact" in st.session_state:
        imp = st.session_state["impact"]
        st.subheader("Card impact")
        st.caption("Each card replaced by a blank and the same games replayed. Numbers are for games where you "
                   "drew the card; ± is the noise.")
        st.dataframe(pd.DataFrame([{
            "Card": c["name"], "Drawn": pct(c["drawn_rate"]),
            "Damage saved": f"{c['defense_score']:+.1f} ± {c['defense_noise']:.1f}",
            "Survival (% pts)": f"{100 * c['survival_delta']:+.1f} ± {100 * c['survival_noise']:.1f}",
            "Win (% pts)": f"{100 * c['win_delta']:+.1f} ± {100 * c['win_noise']:.1f}",
        } for c in imp["cards"]]), hide_index=True, width="stretch")
