import unittest
from datetime import datetime

from decktracker import stats


def game(day: int, result: str, mine: str, theirs: str, mode="GT_UNDERGROUND_ARENA", first=1, turns=8,
         deck="run1", drawn=(), opening=(), played=(), opp=()):
    start = datetime(2026, 10, day, 20, 0)
    return {
        "id": day, "started_at": start.isoformat(), "ended_at": start.replace(minute=12).isoformat(),
        "game_type": mode, "result": result, "went_first": first, "turns": turns,
        "my_class": mine, "opp_class": theirs, "opp_name": "x", "deck_source": "arena",
        "deck_id": deck, "deck_name": f"Arena · {mine.title()}",
        "my_cards": list(drawn), "my_opening": list(opening),
        "my_played": [[1, c, False] for c in played], "opp_cards": {"deck": list(opp), "played": []},
    }


GAMES = [
    game(1, "WON", "PRIEST", "MAGE", drawn=["A", "B"], opening=["A"], played=["A"], opp=["Z"]),
    game(1, "WON", "PRIEST", "MAGE", drawn=["A"], opp=["Z"]),
    game(2, "LOST", "PRIEST", "WARRIOR", first=0, turns=14, drawn=["B"], played=["B"], opp=["Z"]),
    game(4, "LOST", "MAGE", "WARRIOR", deck="run2", mode="GT_ARENA"),
    game(4, "WON", "MAGE", "PRIEST", deck=None, mode="GT_RANKED"),
]


class StatsTest(unittest.TestCase):
    def compute(self, **filters):
        return stats.compute(GAMES, now=datetime(2026, 10, 5), **filters)

    def test_summary_and_streaks(self):
        s = self.compute()["summary"]
        self.assertEqual((s["games"], s["wins"], s["losses"], s["winrate"]), (5, 3, 2, 0.6))
        self.assertEqual(s["streaks"]["best_win"], 2)
        self.assertEqual(s["streaks"]["worst_loss"], 2)
        self.assertEqual(s["streaks"]["current"], {"result": "WON", "length": 1})
        self.assertEqual(s["coin"]["games"], 1)
        self.assertEqual(s["avg_minutes"], 12.0)

    def test_class_matrix(self):
        m = self.compute()["matrix"]
        self.assertEqual(m["mine"], ["MAGE", "PRIEST"])
        self.assertEqual(m["theirs"], ["MAGE", "PRIEST", "WARRIOR"])
        self.assertEqual(m["cells"]["PRIEST|MAGE"], {"games": 2, "wins": 2, "losses": 0, "winrate": 1.0})
        self.assertEqual(m["rows"]["PRIEST"]["games"], 3)
        self.assertEqual(m["cols"]["WARRIOR"]["winrate"], 0.0)

    def test_filters(self):
        self.assertEqual(self.compute(mode="GT_RANKED")["summary"]["games"], 1)
        self.assertEqual(self.compute(mode="arena")["summary"]["games"], 4)
        self.assertEqual(self.compute(my_class="MAGE")["summary"]["games"], 2)
        self.assertEqual(self.compute(days=2)["summary"]["games"], 2)  # since Oct 3
        # Filter options always describe all games, not the filtered slice.
        self.assertEqual(len(self.compute(mode="GT_RANKED")["filters"]["modes"]), 3)

    def test_daily_fills_gaps(self):
        d = self.compute()["daily"]
        self.assertEqual([x["date"] for x in d], ["2026-10-01", "2026-10-02", "2026-10-03", "2026-10-04"])
        self.assertEqual(d[2]["games"], 0)
        self.assertEqual(d[1]["cumulative_winrate"], round(2 / 3, 4))

    def test_arena_runs(self):
        a = self.compute()["arena"]
        self.assertEqual([(r["deck_id"], r["wins"], r["losses"]) for r in a["runs"]], [("run2", 0, 1), ("run1", 2, 1)])
        self.assertEqual(a["avg_wins"], 1.0)
        self.assertEqual(a["by_class"][0]["my_class"], "PRIEST")

    def test_card_stats(self):
        rows = {r["card"]["id"]: r for r in self.compute()["cards"]}
        self.assertEqual(rows["A"]["drawn"]["winrate"], 1.0)
        self.assertEqual(rows["B"]["drawn"], {"games": 2, "wins": 1, "losses": 1, "winrate": 0.5})
        self.assertEqual(rows["A"]["impact"], 0.4)
        opp = {r["card"]["id"]: r for r in self.compute()["opponent_cards"]}
        self.assertEqual(opp["Z"]["games"], 3)

    def test_length_buckets(self):
        lengths = {b["label"]: b["games"] for b in self.compute()["by_length"]}
        self.assertEqual(lengths["6–8"], 4)
        self.assertEqual(lengths["12–14"], 1)


if __name__ == "__main__":
    unittest.main()
