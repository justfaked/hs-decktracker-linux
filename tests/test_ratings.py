import unittest
from datetime import timedelta

from decktracker.ratings import Ratings, parse_tierlist
from tests.helpers import START, LogWriter
from tests.test_tracker import run_session


def card(card_id: str, name: str, score: int) -> str:
    return (f'<li><dl class="card score_{score}"><dt class="any commons" data-toggle="popover" '
            f'data-card-image="https://cdn.heartharena.com/images/renders/enUS/{card_id}.webp">{name} </dt>'
            f'<dd class="score score_{score}">{score}</dd></dl></li>')


# Same markup as heartharena.com/tierlist, trimmed to two classes.
PAGE = (
    '<section class="tab tierlist priest active" id="priest"><ul class="tiers">'
    '<li class="tier great"><header class="tier-header-9">Great</header><ol class="cards">'
    + card("CARD_A", "Alpha", 110) +
    '</ol></li><li class="tier bad"><header class="tier-header-4">Bad</header><ol class="cards">'
    + card("CARD_B", "Bravo", 40) +
    '</ol></li></ul></section>'
    '<section class="tab tierlist any" id="any"><ul class="tiers">'
    '<li class="tier average"><header class="tier-header-6">Average</header><ol class="cards">'
    + card("CARD_C", "Charlie", 75) + card("CARD_A", "Alpha", 90) +
    '</ol></li></ul></section>'
)

RATINGS = Ratings(parse_tierlist(PAGE))


def draft_log(redraft: bool = False) -> str:
    w = LogWriter(START + timedelta(seconds=5))
    w.raw("DraftManager.OnBegin - Got new draft deck with ID: 50")
    w.raw("SetDraftMode - DRAFTING")
    w.raw("Client chooses: Anduin Wrynn (HERO_09)")
    w.raw("DraftManager.OnChosen(): hero=HERO_09")
    w.raw("Client chooses: Alpha (CARD_A)")
    w.raw("Client chooses: Bravo (CARD_B)")
    if redraft:
        w.raw("SetDraftMode - ACTIVE_DRAFT_DECK")
        w.raw("SetDraftMode - REDRAFTING")
        w.raw("DraftManager.OnRedraftBegin - Got new redraft deck with ID: 51")
        w.raw("DraftManager.OnChoicesAndContents - Draft Deck ID: 50, Hero Card = HERO_09")
        w.raw("DraftManager.OnChoicesAndContents - Draft deck contains card CARD_A")
        w.raw("DraftManager.OnChoicesAndContents - Draft deck contains card CARD_B")
        w.raw("Client chooses: Charlie (CARD_C)")
    return w.text()


class RatingsTest(unittest.TestCase):
    def test_parse_by_class_with_tiers(self):
        self.assertEqual(RATINGS.get("PRIEST", "CARD_A"), (110, "Great"))
        self.assertEqual(RATINGS.get("PRIEST", "CARD_B"), (40, "Bad"))

    def test_falls_back_to_class_agnostic_list(self):
        self.assertEqual(RATINGS.get("PRIEST", "CARD_C"), (75, "Average"))
        self.assertEqual(RATINGS.get("MAGE", "CARD_A"), (90, "Average"))
        self.assertIsNone(RATINGS.get("PRIEST", "CARD_Z"))

    def test_draft_view(self):
        tracker = run_session({"Arena": draft_log()})
        tracker.ratings = RATINGS
        state = tracker.snapshot()
        self.assertEqual(state["status"], "drafting")
        draft = state["draft"]
        self.assertEqual(draft["cls"], "PRIEST")
        self.assertEqual(draft["count"], 2)
        self.assertEqual([p["card"]["id"] for p in draft["picks"]], ["CARD_B", "CARD_A"])  # newest first
        self.assertEqual(draft["average"], 75.0)
        self.assertEqual([r["card"]["id"] for r in draft["tierlist"]], ["CARD_A", "CARD_B"])
        rows = {r["card"]["id"]: r for r in state["my_deck"]["rows"]}
        self.assertEqual(rows["CARD_A"]["rating"], {"score": 110, "tier": "Great"})

    def test_redraft_picks_kept_separate(self):
        tracker = run_session({"Arena": draft_log(redraft=True)})
        tracker.ratings = RATINGS
        draft = tracker.snapshot()["draft"]
        self.assertEqual(draft["mode"], "REDRAFTING")
        self.assertEqual(draft["count"], 2)
        self.assertEqual([(p["card"]["id"], p["redraft"]) for p in draft["picks"]][0], ("CARD_C", True))

    def test_redraft_picks_shown_for_exact_deck(self):
        tracker = run_session({"Arena": draft_log(redraft=True)})
        tracker.decks.set_exact_arena("50", "HERO_09", ["CARD_A", "CARD_A", "CARD_B"], START)
        draft = tracker.snapshot()["draft"]
        self.assertEqual(draft["count"], 3)
        self.assertEqual([(p["card"]["id"], p["redraft"]) for p in draft["picks"]][0], ("CARD_C", True))

    def test_draft_finished_returns_to_idle(self):
        text = draft_log() + LogWriter(START + timedelta(minutes=1)).raw("SetDraftMode - ACTIVE_DRAFT_DECK").text()
        state = run_session({"Arena": text}).snapshot()
        self.assertEqual(state["status"], "idle")
        self.assertEqual(state["deck"]["name"], "Arena · Priest")


if __name__ == "__main__":
    unittest.main()
