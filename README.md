# decktracker

A Hearthstone deck tracker for Linux, for Hearthstone running under Proton or Wine (tested on Bazzite).
It reads the game's log files from inside the Wine prefix and shows a live tracker page in your browser.
Nothing runs inside Proton, and nothing reads game memory.

- **My deck:** the cards left in your deck, crossed off as you draw, with the chance to draw each card next.
- **Opponent:** the cards they've played each turn (generated cards are flagged) and the cards revealed from their deck.
- **Arena / Underground Arena:** the deck is detected from `Arena.log`.
- **Constructed:** the deck is detected from `Decks.log` when you queue. You can also paste a deck code.
- **Arena drafting:** shows HearthArena ratings for your picks, with a quick lookup box for the cards you're offered.
- **Stats:** every game is saved to SQLite. The Stats tab can be filtered by period, mode and class, and shows:
  - a class matchup matrix: your class vs. each opponent class, as win rates
  - win rates by mode, by class, going first vs. on the coin, by game length, by time of day and by weekday
  - games per day and your win rate over time
  - Arena runs, with average wins per class and a distribution of runs by wins
  - per-card win rates for your own cards: when drawn, when in your opening hand, when played, and impact vs. your overall win rate
  - your record against the cards opponents showed

  Games you played while the tracker was off are imported from older log folders on the next start.
  Hearthstone keeps only about the last six game sessions in its log folder, so start the tracker at least that often.

It needs only Python 3.10+ and its standard library, so there is nothing to install. That suits Bazzite, where the base system is immutable.

## Setup

```sh
python3 -m decktracker setup   # enables the Power/Decks/Arena logs in log.config (backs up the original)
```

Then restart Hearthstone. You only need to do this once.

## Run

```sh
./decktracker.sh               # starts the tracker and opens http://localhost:8765/
# or
python3 -m decktracker run [--port 8765] [--host 0.0.0.0] [--hs-dir PATH] [--locale enUS]
```

The tracker looks for Hearthstone in the usual places: Steam/Proton `compatdata`, the Steam Flatpak, Lutris (`~/Games`), Bottles, Heroic and `~/.wine`.
If it can't find your install, pass `--hs-dir "/path/to/drive_c/Program Files (x86)/Hearthstone"`.

Use `--host 0.0.0.0` to open the page from a phone or tablet on your network. The page has no login, so only do this on a network you trust.

## Data

| What | Where |
| --- | --- |
| Card data (from HearthstoneJSON, refreshed daily) | `~/.cache/decktracker/` |
| Match history | `~/.local/share/decktracker/history.db` |

## Limitations

- **Arena card counts.** `Arena.log` lists the cards in your arena deck but not how many copies of each.
  The count of cards left in your deck is always exact, and draw chances use it.
  The per-card list gets more accurate as you play, because the tracker learns copy counts from what you draw during the run.
- **Battlegrounds and Mercenaries** are detected but not tracked.
- **Log changes take a restart.** Hearthstone reads `log.config` only at startup.

## Tests

```sh
python3 -m unittest discover -s tests -t .
```
