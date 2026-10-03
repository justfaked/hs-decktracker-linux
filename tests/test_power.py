import unittest

from decktracker.logfile import LINE_RE
from decktracker.power import PowerParser
from tests.helpers import START, arena_game


def parse(text: str) -> tuple[PowerParser, list]:
    ended = []
    parser = PowerParser(on_game_end=ended.append)
    for line in text.splitlines():
        m = LINE_RE.match(line)
        parser.feed(START, m[5])
    return parser, ended


class PowerParserTest(unittest.TestCase):
    def test_players_and_result(self):
        parser, ended = parse(arena_game())
        self.assertEqual(len(ended), 1)
        game = ended[0]
        self.assertEqual(game.game_type, "GT_UNDERGROUND_ARENA")
        self.assertEqual(game.friendly.name, "Me#1234")
        self.assertEqual(game.opponent.name, "Opp#999")  # resolved from UNKNOWN HUMAN PLAYER
        self.assertEqual(game.result(game.friendly), "WON")
        self.assertEqual(game.result(game.opponent), "LOST")
        self.assertEqual(game.hero_card(game.friendly), "HERO_09")
        self.assertEqual(game.hero_class(game.opponent), "MAGE")

    def test_starting_deck_and_transforms(self):
        _, (game,) = parse(arena_game())
        mine = {e.id for e in game.entities.values() if e.in_starting_deck and e.owner == 1}
        theirs = {e.id for e in game.entities.values() if e.in_starting_deck and e.owner == 2}
        self.assertEqual(mine, {4, 5, 6, 7})
        self.assertEqual(theirs, {8, 9, 10, 11})
        # Entity 5 transformed in hand but still counts as the card it was drawn as.
        self.assertEqual(game.entities[5].card_id, "CARD_Z")
        self.assertEqual(game.entities[5].known_id, "CARD_A")
        # The Coin was created mid-game, so it isn't part of their deck.
        self.assertFalse(game.entities[14].in_starting_deck)

    def test_plays_recorded_with_turn(self):
        _, (game,) = parse(arena_game())
        plays = [(p.turn, p.entity_id, p.player_id) for p in game.plays]
        self.assertEqual(plays, [(2, 8, 2), (2, 14, 2)])

    def test_rewind_restores_zones(self):
        _, (game,) = parse(arena_game(rewind=True))
        self.assertEqual(game.entities[5].zone, "DECK")
        self.assertEqual(game.entities[5].known_id, "CARD_A")  # remembered although hidden again
        self.assertEqual(game.entities[14].zone, "REMOVEDFROMGAME")
        self.assertEqual(game.friendly.name, "Me#1234")


if __name__ == "__main__":
    unittest.main()
