"""Builders for synthetic Hearthstone logs in the real line format."""

from datetime import datetime, timedelta

from decktracker.cards import CardDB

SESSION = "Hearthstone_2026_10_03_19_00_00"
START = datetime(2026, 10, 3, 19, 0, 0)

CARDS = CardDB([
    {"id": "HERO_09", "dbfId": 813, "name": "Anduin Wrynn", "type": "HERO", "cardClass": "PRIEST"},
    {"id": "HERO_08", "dbfId": 637, "name": "Jaina Proudmoore", "type": "HERO", "cardClass": "MAGE"},
    {"id": "CARD_A", "dbfId": 1001, "name": "Alpha", "cost": 1, "type": "MINION", "rarity": "COMMON", "cardClass": "PRIEST"},
    {"id": "CARD_B", "dbfId": 1002, "name": "Bravo", "cost": 2, "type": "SPELL", "rarity": "RARE", "cardClass": "PRIEST"},
    {"id": "CARD_C", "dbfId": 1003, "name": "Charlie", "cost": 3, "type": "MINION", "rarity": "EPIC", "cardClass": "NEUTRAL"},
    {"id": "CARD_X", "dbfId": 2001, "name": "X-Ray", "cost": 4, "type": "MINION", "rarity": "COMMON", "cardClass": "MAGE"},
    {"id": "CARD_Z", "dbfId": 2002, "name": "Zulu", "cost": 5, "type": "MINION", "rarity": "COMMON", "cardClass": "NEUTRAL"},
    {"id": "GAME_005", "dbfId": 1746, "name": "The Coin", "cost": 0, "type": "SPELL", "cardClass": "NEUTRAL"},
])


class LogWriter:
    """Accumulates timestamped lines; each call advances the clock by `step`."""

    def __init__(self, start: datetime = START, step: timedelta = timedelta(milliseconds=10)):
        self.t = start
        self.step = step
        self.lines: list[str] = []

    def raw(self, text: str) -> "LogWriter":
        stamp = self.t.strftime("%H:%M:%S.%f") + "0"
        self.lines.append(f"D {stamp} {text}")
        self.t += self.step
        return self

    def power(self, text: str, indent: int = 0) -> "LogWriter":
        return self.raw("GameState.DebugPrintPower() - " + "    " * indent + text)

    def game(self, text: str) -> "LogWriter":
        return self.raw("GameState.DebugPrintGame() - " + text)

    def entity(self, eid: int, card_id: str = "", **tags) -> "LogWriter":
        self.power(f"FULL_ENTITY - Creating ID={eid} CardID={card_id}")
        for tag, value in tags.items():
            self.power(f"tag={tag} value={value}", 1)
        return self

    def tag(self, ref, tag: str, value, indent: int = 0) -> "LogWriter":
        return self.power(f"TAG_CHANGE Entity={ref} tag={tag} value={value} ", indent)

    def show(self, ref, card_id: str, **tags) -> "LogWriter":
        self.power(f"SHOW_ENTITY - Updating Entity={ref} CardID={card_id}")
        for tag, value in tags.items():
            self.power(f"tag={tag} value={value}", 1)
        return self

    def text(self) -> str:
        return "\n".join(self.lines) + "\n"


def ref(eid: int, player: int, zone: str = "DECK", card_id: str = "") -> str:
    return (f"[entityName=UNKNOWN ENTITY [cardType=INVALID] id={eid} zone={zone} zonePos=0 "
            f"cardId={card_id} player={player}]")


def arena_log(deck_id: str = "42", cards=("CARD_A", "CARD_B", "CARD_C")) -> str:
    w = LogWriter(START + timedelta(seconds=5))
    w.raw(f"DraftManager.OnChoicesAndContents - Draft Deck ID: {deck_id}, Hero Card = HERO_09")
    for card in cards:
        w.raw(f"DraftManager.OnChoicesAndContents - Draft deck contains card {card}")
    w.raw("SetDraftMode - ACTIVE_DRAFT_DECK")
    return w.text()


def arena_game(start: datetime = START + timedelta(seconds=30), my_draws=("CARD_A", "CARD_A"),
               rewind: bool = False) -> str:
    """A short Underground Arena game: we (Me#1234, player 1) win against a Mage.

    Our deck: entities 4-7. Theirs: 8-11. Heroes 12 (ours) and 13 (theirs).
    """
    w = LogWriter(start)
    w.power("CREATE_GAME")
    w.power("GameEntity EntityID=1", 1)
    w.power("tag=CARDTYPE value=GAME", 2)
    w.power("tag=ZONE value=PLAY", 2)
    for eid, pid, lo in ((2, 1, 111), (3, 2, 222)):
        w.power(f"Player EntityID={eid} PlayerID={pid} GameAccountId=[hi=1 lo={lo}]", 1)
        w.power(f"tag=CONTROLLER value={pid}", 2)
        w.power(f"tag=HERO_ENTITY value={10 + eid}", 2)
        if pid == 1:
            w.power("tag=FIRST_PLAYER value=1", 2)
    for eid in range(4, 12):
        w.entity(eid, ZONE="DECK", CONTROLLER=1 if eid < 8 else 2)
    w.entity(12, "HERO_09", ZONE="PLAY", CONTROLLER=1, CLASS="PRIEST")
    w.entity(13, "HERO_08", ZONE="PLAY", CONTROLLER=2, CLASS="MAGE")
    w.game("GameType=GT_UNDERGROUND_ARENA")
    w.game("FormatType=FT_WILD")
    w.game("PlayerID=1, PlayerName=Me#1234")
    w.game("PlayerID=2, PlayerName=UNKNOWN HUMAN PLAYER")

    w.tag("GameEntity", "STEP", "BEGIN_MULLIGAN")
    w.tag(4, "ZONE", "HAND")
    w.show(4, my_draws[0], ZONE="HAND", CONTROLLER=1)
    w.tag(8, "ZONE", "HAND")
    w.tag("GameEntity", "STEP", "MAIN_READY")
    w.tag("GameEntity", "TURN", 1)

    # Our turn 1: draw entity 5, then it transforms in hand.
    w.tag(5, "ZONE", "HAND")
    w.show(ref(5, 1), my_draws[1], ZONE="HAND", CONTROLLER=1)
    w.power(f"CHANGE_ENTITY - Updating Entity={ref(5, 1, 'HAND', my_draws[1])} CardID=CARD_Z")
    w.tag("GameEntity", "TURN", 2)

    # Their turn: the real name appears, they play a deck card and the Coin.
    w.tag("Opp#999", "NUM_CARDS_PLAYED_THIS_TURN", 0)
    w.show(ref(8, 2, "HAND"), "CARD_X", ZONE="HAND", CONTROLLER=2)
    w.tag(ref(8, 2, "HAND", "CARD_X"), "ZONE", "PLAY")
    w.entity(14, "GAME_005", ZONE="HAND", CONTROLLER=2)
    w.tag(14, "ZONE", "PLAY")
    w.tag(14, "ZONE", "GRAVEYARD")

    if rewind:
        # Rewind to before our draw: entity 5 goes back to the deck, the Coin never existed.
        w.power("BLOCK_START BlockType=GAME_RESET Entity=GameEntity EffectCardId=System.Collections")
        w.power("RESET_GAME", 1)
        w.power("FULL_ENTITY - Updating Me CardID=", 1)
        w.power("FULL_ENTITY - Updating Opp CardID=", 1)
        for eid in range(4, 14):
            zone = {4: "HAND", 5: "DECK", 8: "PLAY", 12: "PLAY", 13: "PLAY"}.get(eid, "DECK")
            w.power(f"FULL_ENTITY - Updating {ref(eid, 1 if eid in (4, 5, 6, 7, 12) else 2)} CardID=", 1)
            w.power(f"tag=ZONE value={zone}", 2)
        w.power("BLOCK_END")

    w.tag("Me#1234", "PLAYSTATE", "WON")
    w.tag("Opp#999", "PLAYSTATE", "LOST")
    w.tag("GameEntity", "STATE", "COMPLETE")
    return w.text()
