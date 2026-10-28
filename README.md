# MTG Deck Building Simulation

A Commander (EDH) deck simulator. Give it a deck list and it will:

1. **Read the deck** and pull every card's data from [Scryfall](https://scryfall.com).
2. **Tag each card's role**: ramp, draw, removal, board wipes, counterspells, and defense tags such as
   attack taxes (Propaganda, Ghostly Prison), damage prevention, fogs and goad.
3. **Work out opening-hand stats**: land counts, the chance to have each role by each turn, and which cards
   make the best (and worst) opening hands, from thousands of solo "goldfish" games.
4. **Advise on mulligans** with a model trained on your deck: keep or mulligan, and which cards to put on the
   bottom.
5. **Find combos** with [Commander Spellbook](https://commanderspellbook.com), including combos you're one card
   away from.
6. **Simulate matchups**: your deck against one to three opponents (your friends' decks or built-in practice
   decks), with a **defense score** and ranking for every card, measured by replaying the same games with that
   card blanked out.

The simulator itself uses only standard Python (no extra packages), so it can later be packaged into other apps,
including an Android app.

---

## Setup (one time)

1. **Install Python 3.10 or newer** from [python.org](https://www.python.org/downloads/).
   On Windows, tick **"Add Python to PATH"** in the installer.
2. **Download this project.** Either click **Code → Download ZIP** on GitHub and unzip it, or run:
   ```
   git clone https://github.com/Jreyna4/MTG-Deck-Building-Simulation.git
   cd MTG-Deck-Building-Simulation
   ```
3. **Install the extras** (only needed for the app and the tests):
   ```
   pip install -r requirements.txt
   ```
   On Mac/Linux you may need `python3` and `pip3` instead of `python` and `pip`.

The first time you load a deck, card data is downloaded from Scryfall and saved to
`~/.mtgsim/card_cache.json` (your home folder), so later runs are fast and work offline.

## Using it

### Your deck list

Export your deck as text from Moxfield, Archidekt or MTG Arena and save it as a `.txt` file in the `decks`
folder. Mark the commander with a `COMMANDER:` line (Moxfield does this for you):

```
1 Sol Ring (FDC) 286
10 Plains
...
COMMANDER:
1 Kros, Defense Contractor
```

Or pass `--commander "Card Name"`. Set codes, collector numbers and foil markers are fine.
`decks/kros_pillow_fort_v5.txt` is included as an example.

### The app

```
streamlit run app.py
```

A page opens in your browser. Paste a deck list in the sidebar and use the tabs: **Deck & roles**,
**Opening hands**, **Mulligan advisor**, **Combos** and **Matchup**.

### The command line

Every feature also works from a terminal:

```
python -m mtgsim analyze decks/kros_pillow_fort_v5.txt
python -m mtgsim tags decks/kros_pillow_fort_v5.txt --legend
python -m mtgsim mulligan decks/kros_pillow_fort_v5.txt --hand "Island; Plains; Forest; Propaganda; Sol Ring; Swords to Plowshares; Cultivate"
python -m mtgsim combos decks/kros_pillow_fort_v5.txt
python -m mtgsim matchup decks/kros_pillow_fort_v5.txt --vs tokens stompy voltron
python -m mtgsim matchup decks/kros_pillow_fort_v5.txt --vs decks/friend1.txt decks/friend2.txt --impact defense --workers 4
```

| Command | What it does |
| --- | --- |
| `analyze` | Roles, every card's tags, opening-hand odds and goldfish results |
| `tags` | Just the tags (`--legend` explains them) |
| `mulligan` | Keep or mulligan a 7-card hand. Add `--mulligans 1` if you've already taken one |
| `combos` | Combos in the deck, combos one card away, and the cards that would add the most combos |
| `matchup` | Multiplayer games against 1-3 opponents (`--vs` takes deck files or `tokens`, `stompy`, `voltron`) |

Useful `matchup` options:

* `--impact defense` measures every defensive card; `--impact all` measures every card;
  `--impact "Propaganda;Sol Ring"` measures just those.
* `--impact-games 500` uses more games per card for steadier numbers (default 300).
* `--workers 4` uses 4 CPU cores to finish faster.
* `--log 7` prints a full play-by-play of one game (game number 7) so you can see what the AI did.
* `--smart-mulligans` makes your deck mulligan with the trained model instead of the simple rule.
* `--combos` lets decks win when they assemble a Commander Spellbook combo.
* `--json results.json` saves the numbers to a file.

## How it works

| File | Job |
| --- | --- |
| `mtgsim/deck.py` | Reads deck list text |
| `mtgsim/scryfall.py` | Downloads card data (75 cards per request) and caches it |
| `mtgsim/tagger.py` | Reads each card's rules text and tags its roles |
| `mtgsim/engine.py` | A simplified multiplayer game: lands, ramp, casting, combat, taxes, goad, removal... |
| `mtgsim/handstats.py` | Exact opening-hand probabilities |
| `mtgsim/goldfish.py` | Solo games: how fast the deck develops, which cards help or hurt an opening hand |
| `mtgsim/model.py` | A small learning model (ridge regression) in plain Python |
| `mtgsim/mulligan.py` | Trains the model on your deck and gives keep/mulligan advice |
| `mtgsim/combos.py` | Commander Spellbook lookups |
| `mtgsim/archetypes.py` | Built-in practice opponents (tokens, stompy, voltron) |
| `mtgsim/matchup.py` | Matchup games and card-impact (defense) scores |
| `mtgsim/cli.py`, `app.py` | Command line and web app |

**Goldfish score.** The mana you put to use in turns 1-5: mana spent on spells plus mana held up for instants
you're holding. A smooth hand scores higher.

**Mulligan advisor.** It plays a few thousand goldfish games from random hands and learns how much each feature
(lands, ramp, cheap plays, colors...) adds to the score. It uses that to work out what a mulligan is worth on
average (Commander's first mulligan is free; after that it's London rules). For your hand it plays the exact hand
hundreds of times and keeps it if it beats the mulligan.

**Defense score.** For each card, the simulator plays the same set of games twice: once with the card and once
with it replaced by a blank. Using the same shuffles both times means the difference comes from the card. Over the
games where you drew the card, it reports damage saved, change in survival rate and change in win rate, each with a
"±" noise value. Differences smaller than about twice the noise could be luck; run more games to be sure.

## Limits (read before trusting the numbers)

* **It's not a full rules engine.** The tagger understands the common wordings for the effects above. Cards with
  unusual effects still take a slot and cost mana but may do nothing in the simulation. Examples from the Kros
  list: Willbreaker's stealing, Reins of Power, Simic Ascendancy's alternate win, As Foretold, Clockspinning,
  Lavinia's spell restriction. Run `tags` to see what each card was understood as.
* **The AI is simple.** Players develop their board by priority, attack whoever is the best target after
  attack taxes and blockers, hold up counterspells, removal and fogs, and use removal on the biggest threat
  (including your Propaganda-style enchantments). Politics, bluffs and deals aren't modeled.
* **Practice opponents are generic.** For real answers, test against your playgroup's actual decks.
* **Games stop after 15 rounds.** Pillow-fort cards can raise survival but lower win rate, because games stall
  and hit the round limit. Look at survival and damage saved as well as wins.
* **Fixing a wrong tag.** Copy `tag_overrides.example.json`, edit it, and pass it with `--overrides my_file.json`.

## Running the tests

```
python -m pytest
```

The tests use built-in cards, so they don't need the internet.

## Roadmap

* Full-rules deck vs deck by driving [Forge](https://github.com/Card-Forge/forge)'s command-line simulation mode.
* Smarter AI and a stronger learning model (for example a neural network for mulligans or attack decisions).
* Android app. The core uses only the Python standard library, so it can be packaged with
  [BeeWare](https://beeware.org) or Kivy, or run behind a small web API that a phone app calls.

## License

MIT, see `LICENSE`. Card data comes from Scryfall and combo data from Commander Spellbook; please follow their
API guidelines. Magic: The Gathering is a trademark of Wizards of the Coast.
