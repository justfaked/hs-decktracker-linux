import unittest

from tests.helpers import START, LogWriter, ref
from tests.test_secrets import ME, OPP, block, card, turn
from tests.test_tracker import run_session


def hand_game() -> str:
    """We (player 1) face player 2, who collects cards in every way we track, one hand position each."""
    w = LogWriter(START)
    w.power("CREATE_GAME")
    w.power("GameEntity EntityID=1", 1)
    w.power("tag=ZONE value=PLAY", 2)
    for eid, pid, lo in ((2, 1, 111), (3, 2, 222)):
        w.power(f"Player EntityID={eid} PlayerID={pid} GameAccountId=[hi=1 lo={lo}]", 1)
        w.power(f"tag=CONTROLLER value={pid}", 2)
    for eid in range(4, 12):
        w.entity(eid, ZONE="DECK", CONTROLLER=1 if eid < 6 else 2)
    w.game("GameType=GT_UNDERGROUND_ARENA")
    w.game(f"PlayerID=1, PlayerName={ME}")
    w.game("PlayerID=2, PlayerName=UNKNOWN HUMAN PLAYER")
    w.tag(5, "ZONE", "HAND")
    w.show(5, "CARD_B", ZONE="HAND", CONTROLLER=1, CARDTYPE="SPELL")

    def arrive(eid, position):
        w.tag(ref(eid, 2), "ZONE_POSITION", position, indent=1)
        w.tag(ref(eid, 2), "ZONE", "HAND", indent=1)

    w.tag("GameEntity", "STEP", "BEGIN_DRAW")
    arrive(6, 1)  # opening hand
    w.tag("GameEntity", "STEP", "BEGIN_MULLIGAN")
    arrive(7, 2)  # replacement for a mulliganed card
    w.entity(12, "", ZONE="HAND", CONTROLLER=2, ZONE_POSITION=3)  # the Coin, hidden

    turn(w, OPP, ME, 2)
    block(w, "TRIGGER", OPP)
    arrive(8, 4)  # drawn at the start of their turn
    w.power("BLOCK_END")
    w.show(ref(9, 2), "CARD_X", ZONE="PLAY", CONTROLLER=2, CARDTYPE="MINION")
    w.show(ref(10, 2), "CARD_Z", ZONE="PLAY", CONTROLLER=2, CARDTYPE="MINION")
    block(w, "TRIGGER", card(10, 2, "PLAY", "CARD_Z"))
    arrive(11, 5)  # drawn by their minion's effect
    w.power("BLOCK_END")

    turn(w, ME, OPP, 3)
    block(w, "PLAY", card(5, 1, "HAND", "CARD_B"))
    w.tag(card(9, 2, "PLAY", "CARD_X"), "ZONE_POSITION", 6, indent=1)
    w.tag(card(9, 2, "PLAY", "CARD_X"), "ZONE", "HAND", indent=1)  # bounced by our spell
    w.entity(13, "", ZONE="HAND", CONTROLLER=2, ZONE_POSITION=7)  # given to them by our spell
    w.tag(13, "DISPLAYED_CREATOR", 5, indent=1)
    w.power("BLOCK_END")
    return w.text()


class HandTest(unittest.TestCase):
    def test_how_and_when_each_card_arrived(self):
        hand = run_session({"Power": hand_game()}).snapshot()["opponent"]["hand"]
        self.assertEqual(
            [(h["turn"], h["how"], h["card"] and h["card"]["name"], h["by"] and h["by"]["name"]) for h in hand],
            [(None, "opening", None, None),
             (None, "mulligan", None, None),
             (None, "start", None, None),
             (1, "drawn", None, None),
             (1, "drawn", None, "Zulu"),
             (2, "returned", "X-Ray", "Bravo"),
             (2, "created", None, "Bravo")])


if __name__ == "__main__":
    unittest.main()
