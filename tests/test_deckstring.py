import unittest

from decktracker import deckstring

# Example deck code from HearthSim's python-hearthstone test suite.
HUNTER = "AAECAR8GxwPJBLsFmQfZB/gIDI0B2AGoArUDhwSSBe0G6wfbCe0JgQr+DAA="


class DeckstringTest(unittest.TestCase):
    def test_decode_known_deck(self):
        deck = deckstring.decode(HUNTER)
        self.assertEqual(deck.heroes, [31])
        self.assertEqual(deck.format, 2)
        self.assertEqual(sum(deck.cards.values()), 30)
        self.assertEqual(deck.cards[455], 1)
        self.assertEqual(deck.cards[141], 2)

    def test_roundtrip(self):
        original = deckstring.DeckDefinition(
            heroes=[7], cards={1: 1, 300: 2, 70000: 1, 5: 3}, format=1,
            sideboards=[(900, 1, 70000), (901, 2, 70000)],
        )
        decoded = deckstring.decode(deckstring.encode(original))
        self.assertEqual(decoded.heroes, original.heroes)
        self.assertEqual(decoded.cards, original.cards)
        self.assertEqual(decoded.format, 1)
        self.assertEqual(sorted(decoded.sideboards), sorted(original.sideboards))

    def test_encode_matches_reference(self):
        self.assertEqual(deckstring.encode(deckstring.decode(HUNTER)), HUNTER)

    def test_rejects_garbage(self):
        with self.assertRaises(ValueError):
            deckstring.decode("aGVsbG8gd29ybGQ=")


if __name__ == "__main__":
    unittest.main()
