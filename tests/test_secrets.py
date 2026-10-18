import unittest

from decktracker.ratings import Ratings
from decktracker.secrets import ROOM, SecretInfo, Survived
from tests.helpers import START, LogWriter, ref
from tests.test_tracker import run_session

ME, OPP = "Me#1234", "Opp#999"
SECRET, SECOND_SECRET = 8, 10
THEIR_MINION, OUR_MINION, OUR_NEW_MINION, OUR_SPELL = 9, 6, 4, 5


def card(eid: int, player: int, zone: str, card_id: str) -> str:
    return f"[entityName={card_id} id={eid} zone={zone} zonePos=1 cardId={card_id} player={player}]"


def block(w: LogWriter, kind: str, entity: str, target: str = "0") -> None:
    w.power(f"BLOCK_START BlockType={kind} Entity={entity} EffectCardId=System.Collections.Generic.List`1"
            f"[System.String] EffectIndex=0 Target={target} SubOption=-1")


def turn(w: LogWriter, player: str, other: str, number: int) -> None:
    w.tag("GameEntity", "STEP", "MAIN_END")
    w.tag("GameEntity", "STEP", "MAIN_NEXT")
    w.tag(other, "CURRENT_PLAYER", 0, indent=1)
    w.tag(player, "CURRENT_PLAYER", 1, indent=1)
    w.tag("GameEntity", "TURN", number)
    w.tag("GameEntity", "STEP", "MAIN_READY")
    w.tag("GameEntity", "STEP", "MAIN_START_TRIGGERS")
    w.tag("GameEntity", "STEP", "MAIN_ACTION")


def secret_game(our_turn=lambda w: None, secrets=(SECRET,), their_minions=1,
                game_type="GT_UNDERGROUND_ARENA") -> str:
    """We (player 1, Priest) face a Mage who plays hidden secrets, then it's our turn.

    Ours: minion 4 and spell 5 in hand, minion 6 on the board. Theirs: secrets from
    their deck (8, 10), minion 9 on the board plus `their_minions - 1` more (20+).
    """
    w = LogWriter(START)
    w.power("CREATE_GAME")
    w.power("GameEntity EntityID=1", 1)
    w.power("tag=ZONE value=PLAY", 2)
    for eid, pid, lo in ((2, 1, 111), (3, 2, 222)):
        w.power(f"Player EntityID={eid} PlayerID={pid} GameAccountId=[hi=1 lo={lo}]", 1)
        w.power(f"tag=CONTROLLER value={pid}", 2)
        w.power(f"tag=HERO_ENTITY value={10 + eid}", 2)
    for eid in range(4, 12):
        w.entity(eid, ZONE="DECK", CONTROLLER=1 if eid < 8 else 2)
    w.entity(12, "HERO_09", ZONE="PLAY", CONTROLLER=1, CARDTYPE="HERO", CLASS="PRIEST")
    w.entity(13, "HERO_08", ZONE="PLAY", CONTROLLER=2, CARDTYPE="HERO", CLASS="MAGE")
    w.game(f"GameType={game_type}")
    w.game(f"PlayerID=1, PlayerName={ME}")
    w.game("PlayerID=2, PlayerName=UNKNOWN HUMAN PLAYER")
    w.tag("GameEntity", "STEP", "BEGIN_MULLIGAN")
    for eid, card_id, zone, kind in ((4, "CARD_A", "HAND", "MINION"), (5, "CARD_B", "HAND", "SPELL"),
                                     (6, "CARD_C", "PLAY", "MINION")):
        w.tag(eid, "ZONE", zone)
        w.show(eid, card_id, ZONE=zone, CONTROLLER=1, CARDTYPE=kind)

    turn(w, OPP, ME, 2)
    w.show(ref(THEIR_MINION, 2), "CARD_X", ZONE="PLAY", CONTROLLER=2, CARDTYPE="MINION")
    for i in range(their_minions - 1):
        w.entity(20 + i, "CARD_Z", ZONE="PLAY", CONTROLLER=2, CARDTYPE="MINION")
    for eid in secrets:
        w.tag(ref(eid, 2), "ZONE", "HAND")
        block(w, "PLAY", ref(eid, 2, "HAND"))
        w.tag(ref(eid, 2, "HAND"), "SECRET", 1, indent=1)
        w.tag(ref(eid, 2, "HAND"), "CLASS", "MAGE", indent=1)
        w.tag(ref(eid, 2, "HAND"), "ZONE", "SECRET", indent=1)
        w.power("BLOCK_END")

    turn(w, ME, OPP, 3)
    our_turn(w)
    return w.text()


def play(w: LogWriter, eid: int, card_id: str, zone: str, target: str = "0", inside=lambda w: None) -> None:
    block(w, "PLAY", card(eid, 1, "HAND", card_id), target)
    w.tag(card(eid, 1, "HAND", card_id), "ZONE", zone, indent=1)
    inside(w)
    w.power("BLOCK_END")


def cast_spell(w: LogWriter, inside=lambda w: None) -> None:
    play(w, OUR_SPELL, "CARD_B", "GRAVEYARD", inside=inside)


def play_minion(w: LogWriter) -> None:
    play(w, OUR_NEW_MINION, "CARD_A", "PLAY")


def attack(w: LogWriter, target: str) -> None:
    block(w, "ATTACK", card(OUR_MINION, 1, "PLAY", "CARD_C"), target)
    w.power("BLOCK_END")


def reveal(eid: int, card_id: str):
    def inside(w: LogWriter) -> None:
        w.show(ref(eid, 2, "SECRET"), card_id)
        w.tag(ref(eid, 2, "SECRET", card_id), "ZONE", "GRAVEYARD")
    return inside


def secrets(text: str, ratings: Ratings | None = None) -> list[dict]:
    tracker = run_session({"Power": text})
    if ratings is not None:
        tracker.ratings = ratings
    return tracker.snapshot()["opponent"]["secrets"]


def options(secret: dict) -> dict[str, str | None]:
    """{card name: why it's ruled out, or None if still possible}"""
    return {o["card"]["name"]: o["ruled_out"] for o in secret["options"]}


ALL_MAGE = {"Counterspell", "Effigy", "Flames of Infinity", "Ice Barrier", "Ice Block", "Mirror Entity"}


class SecretTest(unittest.TestCase):
    def possible(self, secret: dict) -> set[str]:
        return {name for name, why in options(secret).items() if why is None}

    def test_hidden_secret_lists_its_class_secrets_once(self):
        (secret,) = secrets(secret_game())
        self.assertEqual((secret["cls"], secret["turn"]), ("MAGE", 1))
        self.assertEqual(self.possible(secret), ALL_MAGE)  # one Counterspell, no Hunter secret

    def test_casting_a_spell_rules_out_counterspell(self):
        (secret,) = secrets(secret_game(cast_spell))
        self.assertEqual(self.possible(secret), ALL_MAGE - {"Counterspell"})
        self.assertEqual(options(secret)["Counterspell"], "Would have triggered when you cast a spell (turn 2)")

    def test_playing_a_minion_rules_out_mirror_entity_unless_their_board_is_full(self):
        (secret,) = secrets(secret_game(play_minion))
        self.assertNotIn("Mirror Entity", self.possible(secret))
        (secret,) = secrets(secret_game(play_minion, their_minions=7))
        self.assertIn("Mirror Entity", self.possible(secret))

    def test_attacks(self):
        (secret,) = secrets(secret_game(lambda w: attack(w, card(13, 2, "PLAY", "HERO_08"))))
        self.assertNotIn("Ice Barrier", self.possible(secret))
        (secret,) = secrets(secret_game(lambda w: attack(w, card(THEIR_MINION, 2, "PLAY", "CARD_X"))))
        self.assertIn("Ice Barrier", self.possible(secret))

    def test_their_minion_dying_on_our_turn_rules_out_effigy(self):
        kill = lambda w: w.tag(card(THEIR_MINION, 2, "PLAY", "CARD_X"), "ZONE", "GRAVEYARD", indent=1)
        (secret,) = secrets(secret_game(lambda w: cast_spell(w, kill)))
        self.assertNotIn("Effigy", self.possible(secret))

    def test_end_of_our_turn_rules_out_flames_of_infinity(self):
        (secret,) = secrets(secret_game(lambda w: turn(w, OPP, ME, 4)))
        self.assertNotIn("Flames of Infinity", self.possible(secret))

    def test_nothing_learned_when_a_secret_fires(self):
        (secret,) = secrets(secret_game(lambda w: cast_spell(w, reveal(SECRET, "EX1_287")),
                                        secrets=(SECRET, SECOND_SECRET)))
        # The spell was countered, so other spell secrets couldn't have triggered either.
        # It can't be another Counterspell, though: two copies can't be in play together.
        self.assertEqual(self.possible(secret), ALL_MAGE - {"Counterspell"})
        self.assertIn("Already revealed", options(secret)["Counterspell"])

    def test_secrets_only_trigger_on_our_turn(self):
        def their_minion_dies_on_their_turn(w: LogWriter) -> None:
            turn(w, OPP, ME, 4)
            block(w, "ATTACK", card(THEIR_MINION, 2, "PLAY", "CARD_X"), card(OUR_MINION, 1, "PLAY", "CARD_C"))
            w.tag(card(THEIR_MINION, 2, "PLAY", "CARD_X"), "ZONE", "GRAVEYARD", indent=1)
            w.power("BLOCK_END")
        (secret,) = secrets(secret_game(their_minion_dies_on_their_turn))
        self.assertIn("Effigy", self.possible(secret))

    def test_arena_secrets_limited_to_the_tier_list(self):
        ratings = Ratings({"MAGE": {"EX1_287": [80, "Good"], "EX1_294": [70, "Good"]}})
        (secret,) = secrets(secret_game(), ratings)
        self.assertEqual(self.possible(secret), {"Counterspell", "Mirror Entity"})
        (secret,) = secrets(secret_game(game_type="GT_RANKED"), ratings)
        self.assertEqual(self.possible(secret), ALL_MAGE)

    def test_conditions_must_hold_for_a_rule_out(self):
        info = SecretInfo(survived=[Survived("minion", frozenset(), 3)])
        self.assertIsNone(info.ruled_out("EX1_294"))  # Mirror Entity needs room on their board
        info.survived.append(Survived("minion", frozenset({ROOM}), 4))
        self.assertIn("turn 4", info.ruled_out("CORE_EX1_294"))
        self.assertIsNone(info.ruled_out("EX1_295"))  # Ice Block: no rule, never ruled out


if __name__ == "__main__":
    unittest.main()
