import json
import tempfile
import unittest
from datetime import timedelta
from pathlib import Path

from decktracker import deckstring
from decktracker.history import History
from decktracker.logfile import Session
from decktracker.tracker import Tracker
from tests.helpers import CARDS, SESSION, START, LogWriter, arena_game, arena_log, before_end


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

    def test_win_chance_from_earlier_games_like_this_one(self):
        history = History(":memory:")
        normal_arena = arena_game(start=START).replace("GT_UNDERGROUND_ARENA", "GT_ARENA")
        games = normal_arena + "".join(arena_game(start=START + timedelta(minutes=20 * i)) for i in range(1, 7))
        odds = run_session({"Arena": arena_log(), "Power": games}, history).snapshot()["odds"]
        # Priest vs Mage going first in Underground Arena, won 5 times before; neither the
        # normal Arena game nor the game on screen counts.
        self.assertEqual((odds["level"], odds["winrate"], odds["first"]), ("matchup_order", 1.0, True))
        self.assertEqual(odds["levels"][0]["games"], 5)
        self.assertEqual((odds["my_cls_name"], odds["opp_cls_name"], odds["mode"]),
                         ("Priest", "Mage", "Underground Arena"))

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

    def test_spells_cast_by_school(self):
        game = arena_game(opp_plays=("CARD_F", "CARD_I", "CARD_F", "CARD_X"))
        state = run_session({"Arena": arena_log(), "Power": game}).snapshot()
        # The Coin counts as a spell but has no school; X-Ray is a minion.
        self.assertEqual(state["game"]["opp"]["spells"], {
            "cast": 4, "schools": [{"school": "FIRE", "count": 2}, {"school": "FROST", "count": 1}]})
        self.assertEqual(state["game"]["me"]["spells"], {"cast": 0, "schools": []})

    def test_spells_cast_by_card_effects_are_counted(self):
        # The game's own counter knows of more spells than were played from hand.
        lines = arena_game().splitlines(keepends=True)
        end = next(i for i, line in enumerate(lines) if "Opp#999 tag=PLAYSTATE" in line)
        lines.insert(end, lines[end].replace("tag=PLAYSTATE value=LOST", "tag=NUM_SPELLS_PLAYED_THIS_GAME value=3"))
        state = run_session({"Arena": arena_log(), "Power": "".join(lines)}).snapshot()
        self.assertEqual(state["game"]["opp"]["spells"]["cast"], 3)

    def test_remaining_deck_summary(self):
        state = run_session({"Arena": arena_log(), "Power": arena_game()}).snapshot()
        # Both Alphas are drawn; Bravo (2-mana spell) and Charlie (3-mana 3/4) are left.
        self.assertEqual(state["my_deck"]["summary"], {
            "types": {"spell": 1, "minion": 1}, "curve": [0, 0, 1, 1, 0, 0, 0, 0], "attack": 3, "health": 4,
            "minion_types": {"MURLOC": 1}})

    def test_minion_types_left_count_amalgams_for_every_type(self):
        log = arena_log(cards=("CARD_A", "CARD_B", "CARD_C", "CARD_L"))
        state = run_session({"Arena": log, "Power": arena_game()}).snapshot()
        # Charlie is a Murloc, Lima is of every type.
        self.assertEqual(state["my_deck"]["summary"]["minion_types"], {"MURLOC": 2})

    def test_minion_types_played_and_elemental_streak(self):
        # They're second, so their turns are the even ones: Elementals on turns 2 and 4, none yet on 6.
        game = arena_game(opp_plays=(4, "CARD_Z", 5, 6, "CARD_C"))
        minions = run_session({"Arena": arena_log(), "Power": game}).snapshot()["game"]["opp"]["minions"]
        self.assertEqual(minions["played"], 3)
        self.assertEqual(minions["types"], {"ELEMENTAL": 2, "BEAST": 1, "MURLOC": 1})
        self.assertEqual(minions["elementals"], {"this_turn": False, "last_turn": True, "streak": 2})

    def test_elemental_streak_breaks_on_a_turn_without_one(self):
        game = arena_game(opp_plays=(4, "CARD_C", 5, 6, "CARD_Z"))
        state = run_session({"Arena": arena_log(), "Power": game}).snapshot()
        self.assertEqual(state["game"]["opp"]["minions"]["elementals"],
                         {"this_turn": True, "last_turn": False, "streak": 1})
        # We haven't played a minion.
        self.assertEqual(state["game"]["me"]["minions"], {"played": 0, "types": {}, "elementals": None})

    def test_counters(self):
        w = LogWriter()
        w.tag("Me#1234", "NUM_TIMES_HERO_POWER_USED_THIS_GAME", 2)
        w.tag("Me#1234", "AMOUNT_HEALED_THIS_GAME", 0)
        w.tag("Me#1234", "FATIGUE", 2)
        w.tag("Opp#999", "CORPSES", 3)
        w.tag("Opp#999", "OVERLOAD_THIS_GAME", 4)
        state = run_session({"Arena": arena_log(), "Power": before_end(arena_game(), w)}).snapshot()
        # Counters at 0 are left out; our deck is nearly empty, so the next fatigue hit shows.
        self.assertEqual(state["game"]["me"]["counters"], {"drawn": 1, "hero_power": 2, "fatigue": 3})
        # Corpses only for Death Knights or whoever has spent some.
        self.assertEqual(state["game"]["opp"]["counters"], {"overload": 4, "fatigue": 1})
        w.tag("Opp#999", "CORPSES_SPENT_THIS_GAME", 1)
        state = run_session({"Arena": arena_log(), "Power": before_end(arena_game(), w)}).snapshot()
        self.assertEqual(state["game"]["opp"]["counters"]["corpses"], 3)

    def test_mana_spent_per_turn(self):
        w = LogWriter()
        for player, turn, crystals, locked, spent in (("Opp#999", 3, 1, 0, 1), ("Me#1234", 4, 2, 1, 1),
                                                     ("Opp#999", 5, 2, 0, 3), ("Me#1234", 6, 3, 0, 5)):
            w.tag(player, "CURRENT_PLAYER", 1)
            w.tag("GameEntity", "TURN", turn)
            w.tag("GameEntity", "STEP", "MAIN_READY")
            w.tag(player, "RESOURCES", crystals)
            w.tag(player, "OVERLOAD_LOCKED", locked)
            w.tag(player, "NUM_RESOURCES_SPENT_THIS_GAME", spent)
            if turn < 6:  # the last turn is still going
                w.tag("GameEntity", "STEP", "MAIN_NEXT")
                w.tag(player, "CURRENT_PLAYER", 0)
        state = run_session({"Arena": arena_log(), "Power": before_end(arena_game(), w, finish=False)}).snapshot()
        # Our turns 2 and 3: 1 of 1 crystal (one was overloaded), then 4 so far of 3 (the Coin).
        self.assertEqual(state["me"]["mana"], [{"turn": 2, "spent": 1, "available": 1, "current": False},
                                               {"turn": 3, "spent": 4, "available": 3, "current": True}])

    def test_minions_that_died(self):
        w = LogWriter()
        for eid, card, owner in ((30, "CARD_C", 1), (31, "CORE_CARD_C", 1), (32, "CARD_C", 2)):
            w.entity(eid, card, ZONE="PLAY", CONTROLLER=owner, CARDTYPE="MINION")
            w.tag(eid, "ZONE", "GRAVEYARD")
        w.tag(8, "CARDTYPE", "MINION")
        w.tag(8, "ZONE", "GRAVEYARD")
        state = run_session({"Arena": arena_log(), "Power": before_end(arena_game(), w)}).snapshot()
        # Both of our Charlies were created mid-game; the reprint counts with the original.
        self.assertEqual([(r["card"]["name"], r["total"], r["created"]) for r in state["me"]["died"]],
                         [("Charlie", 2, True)])
        self.assertEqual([(r["card"]["name"], r["total"], r["created"]) for r in state["opponent"]["died"]],
                         [("Charlie", 1, True), ("X-Ray", 1, False)])

    def test_my_played_cards(self):
        w = LogWriter()
        w.tag(4, "ZONE", "PLAY")
        state = run_session({"Arena": arena_log(), "Power": before_end(arena_game(), w)}).snapshot()
        self.assertEqual([(p["turn"], p["card"]["id"], p["created"]) for p in state["me"]["played"]],
                         [(1, "CARD_A", False)])

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
