import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from decktracker import deckstring
from decktracker.history import History
from decktracker.logfile import Session
from decktracker.tracker import Tracker
from tests.helpers import CARDS, SESSION, START, LogWriter, arena_game, arena_log


def run_session(files: dict[str, str], history: History | None = None) -> Tracker:
    with tempfile.TemporaryDirectory() as tmp:
        folder = Path(tmp) / SESSION
        folder.mkdir()
        for kind, text in files.items():
            (folder / f"{kind}.log").write_text(text)
        tracker = Tracker(CARDS, history, SESSION)
        for line in Session(folder).read_new():
            tracker.feed(line)
        return tracker


def rows_by_id(rows):
    return {r["card"]["id"]: r for r in rows}


class TrackerTest(unittest.TestCase):
    def test_arena_game_is_recorded(self):
        history = History(":memory:")
        tracker = run_session({"Arena": arena_log(), "Power": arena_game()}, history)
        (record,) = tracker.recorded
        self.assertEqual(record["result"], "WON")
        self.assertEqual(record["my_class"], "PRIEST")
        self.assertEqual(record["opp_class"], "MAGE")
        self.assertEqual(record["deck_source"], "arena")
        self.assertEqual(record["deck_id"], "42")
        self.assertEqual(record["went_first"], 1)
        self.assertEqual(record["opp_cards"]["played"], [[1, "CARD_X"], [1, "GAME_005"]])
        self.assertEqual(history.games()[0]["result"], "WON")
        self.assertEqual(record["my_opening"], ["CARD_A"])
        self.assertEqual(record["my_played"], [])

    def test_remaining_deck(self):
        tracker = run_session({"Arena": arena_log(), "Power": arena_game()})
        state = tracker.snapshot()
        self.assertEqual(state["status"], "finished")
        self.assertEqual(state["my_deck"]["in_deck"], 2)
        rows = rows_by_id(state["my_deck"]["rows"])
        # Arena.log listed CARD_A once but we drew it twice: total adjusts, none left.
        self.assertEqual((rows["CARD_A"]["total"], rows["CARD_A"]["left"]), (2, 0))
        self.assertEqual((rows["CARD_B"]["total"], rows["CARD_B"]["left"]), (1, 1))
        self.assertEqual(rows["CARD_B"]["chance"], 0.5)

    def test_opponent_view(self):
        state = run_session({"Arena": arena_log(), "Power": arena_game()}).snapshot()
        self.assertEqual(state["game"]["opp"]["name"], "Opp#999")
        self.assertEqual([r["card"]["id"] for r in state["opponent"]["seen"]], ["CARD_X"])
        played = [(p["card"]["id"], p["created"]) for p in state["opponent"]["played"]]
        self.assertEqual(played, [("CARD_X", False), ("GAME_005", True)])

    def test_arena_copy_counts_are_learned(self):
        history = History(":memory:")
        game2 = arena_game(start=START + timedelta(minutes=20), my_draws=("CARD_B", "CARD_C"))
        tracker = run_session({"Arena": arena_log(), "Power": arena_game() + game2}, history)
        self.assertEqual(history.copies("42")["CARD_A"], 2)
        # Second game: CARD_A is now expected twice even though Arena.log lists it once.
        rows = rows_by_id(tracker.snapshot()["my_deck"]["rows"])
        self.assertEqual((rows["CARD_A"]["total"], rows["CARD_A"]["left"]), (2, 2))

    def test_constructed_deck_from_decks_log(self):
        code = deckstring.encode(deckstring.DeckDefinition(heroes=[813], cards={1001: 2, 1002: 1, 1003: 1}))
        decks = LogWriter(START + timedelta(seconds=20))
        decks.raw("Finding Game With Deck:")
        decks.raw("### My Priest")
        decks.raw("# Deck ID: 777")
        decks.raw(code)
        power = arena_game().replace("GT_UNDERGROUND_ARENA", "GT_RANKED")
        (record,) = run_session({"Decks": decks.text(), "Power": power}).recorded
        self.assertEqual(record["deck_source"], "constructed")
        self.assertEqual(record["deck_name"], "My Priest")
        self.assertEqual(record["deck_id"], "777")
        self.assertEqual(sorted(record["deck_cards"]), ["CARD_A", "CARD_A", "CARD_B", "CARD_C"])

    def test_arena_deck_edit_after_redraft(self):
        # Redraft added CARD_Z; then 30 of 35 were kept: CARD_B dropped, CARD_A now twice.
        arena = LogWriter(START + timedelta(seconds=10))
        arena.raw("SetDraftMode - REDRAFTING")
        arena.raw("DraftManager.OnRedraftBegin - Got new redraft deck with ID: 43")
        arena.raw("Client chooses: Zulu (CARD_Z)")
        arena.raw("SetDraftMode - ACTIVE_DRAFT_DECK")
        code = deckstring.encode(deckstring.DeckDefinition(heroes=[813], cards={1001: 2, 1003: 1, 2002: 1}))
        decks = LogWriter(START + timedelta(seconds=20))
        decks.raw("Finished Editing Deck:")
        decks.raw("### ")
        decks.raw("# Deck ID: 42")
        decks.raw(code)
        tracker = run_session({"Arena": arena_log() + arena.text(), "Decks": decks.text()})
        state = tracker.snapshot()
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["deck"]["name"], "Arena · Priest")
        rows = rows_by_id(state["my_deck"]["rows"])
        self.assertEqual({card_id: r["total"] for card_id, r in rows.items()},
                         {"CARD_A": 2, "CARD_C": 1, "CARD_Z": 1})

    def test_editing_another_deck_keeps_arena_deck(self):
        code = deckstring.encode(deckstring.DeckDefinition(heroes=[813], cards={1001: 2}))
        decks = LogWriter(START + timedelta(seconds=20))
        decks.raw("Finished Editing Deck:")
        decks.raw("### My Priest")
        decks.raw("# Deck ID: 777")
        decks.raw(code)
        tracker = run_session({"Arena": arena_log(), "Decks": decks.text()})
        self.assertEqual(sorted(tracker.decks.arena.cards), ["CARD_A", "CARD_B", "CARD_C"])
        self.assertIsNone(tracker.decks.constructed)

    def test_arena_game_uses_exact_deck_from_decks_log(self):
        code = deckstring.encode(deckstring.DeckDefinition(heroes=[813], cards={1001: 2, 1002: 2}))
        decks = LogWriter(START + timedelta(seconds=20))
        decks.raw("Starting Arena Game With Deck:")
        decks.raw("### ")
        decks.raw("# Deck ID: 42")
        decks.raw(code)
        (record,) = run_session({"Arena": arena_log(), "Decks": decks.text(), "Power": arena_game()}).recorded
        self.assertEqual(record["deck_id"], "42")
        self.assertEqual(sorted(record["deck_cards"]), ["CARD_A", "CARD_A", "CARD_B", "CARD_B"])

    def test_idle_shows_upcoming_deck(self):
        state = run_session({"Arena": arena_log()}).snapshot()
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["deck"]["name"], "Arena · Priest")
        self.assertEqual(len(state["my_deck"]["rows"]), 3)

    def test_battlegrounds_not_recorded(self):
        power = arena_game().replace("GT_UNDERGROUND_ARENA", "GT_BATTLEGROUNDS")
        tracker = run_session({"Power": power})
        self.assertEqual(tracker.recorded, [])
        self.assertEqual(tracker.snapshot()["status"], "unsupported")

    def test_history_dedupes_reimports(self):
        history = History(":memory:")
        files = {"Arena": arena_log(), "Power": arena_game()}
        run_session(files, history)
        run_session(files, history)
        (game,) = history.games()
        self.assertEqual(game["deck_name"], "Arena · Priest")
        self.assertEqual(game["opp_cards"]["deck"], ["CARD_X"])  # JSON decoded

    def test_manual_deck(self):
        tracker = run_session({"Power": arena_game().replace("GT_UNDERGROUND_ARENA", "GT_RANKED")})
        code = deckstring.encode(deckstring.DeckDefinition(heroes=[813], cards={1001: 2, 1002: 2}))
        deck = tracker.set_manual_deck(code)
        self.assertEqual(deck.name, "Priest deck")
        self.assertEqual(json.loads(json.dumps(tracker.snapshot()))["deck"]["size"], 4)


if __name__ == "__main__":
    unittest.main()
