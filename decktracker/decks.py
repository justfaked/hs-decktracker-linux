"""Detects which deck is being played from Arena.log and Decks.log."""

import re
from dataclasses import dataclass, field, replace
from datetime import datetime

from . import deckstring
from .cards import CLASS_NAMES, CardDB

RE_ARENA_DECK = re.compile(r"Draft Deck ID: (\d+), Hero Card = (\S+)")
RE_ARENA_CARD = re.compile(r"Draft deck contains card (\S+)")
RE_ARENA_NEW = re.compile(r"Got new draft deck with ID: (\d+)")
RE_ARENA_REDRAFT = re.compile(r"Got new redraft deck with ID: (\d+)")
RE_ARENA_PICK = re.compile(r"Client chooses: .* \((\S+)\)$")
RE_ARENA_MODE = re.compile(r"SetDraftMode - (\S+)")
RE_DECK_NAME = re.compile(r"### (.*)$")
RE_DECK_ID = re.compile(r"# Deck ID: (\d+)")
RE_DECKSTRING = re.compile(r"^(AAE[A-Za-z0-9+/=]+)$")

ARENA_GAME_TYPES = {"GT_ARENA", "GT_UNDERGROUND_ARENA"}
UNSUPPORTED_PREFIXES = ("GT_BATTLEGROUNDS", "GT_MERCENARIES")


@dataclass
class Deck:
    source: str  # "arena", "constructed" or "manual"
    name: str
    deck_id: str = ""
    hero: str = ""  # hero card id
    cards: list[str] = field(default_factory=list)  # one entry per copy
    deckstring: str = ""
    seen_at: datetime = datetime.min
    exact: bool = False  # card counts known exactly (not inferred from Arena.log)

    def copy(self) -> "Deck":
        return replace(self, cards=list(self.cards))


def is_supported(game_type: str) -> bool:
    return not game_type.startswith(UNSUPPORTED_PREFIXES)


def deck_from_code(code: str, cards: CardDB, source: str = "manual", name: str = "",
                   deck_id: str = "") -> Deck:
    """Build a Deck from a deck code; raises ValueError for invalid codes."""
    definition = deckstring.decode(code)
    ids = []
    for dbf, count in definition.cards.items():
        card = cards.by_dbf(dbf)
        ids += [card["id"] if card else f"dbf:{dbf}"] * count
    hero = cards.by_dbf(definition.heroes[0]) if definition.heroes else None
    hero_id = hero["id"] if hero else ""
    if not name:
        cls = cards.class_of(hero_id) if hero_id else None
        name = f"{CLASS_NAMES.get(cls, 'Custom')} deck"
    return Deck(source=source, name=name, deck_id=deck_id, hero=hero_id, cards=ids, deckstring=code.strip())


class DeckDetector:
    def __init__(self, cards: CardDB):
        self.cards = cards
        self.arena: Deck | None = None
        self.constructed: Deck | None = None
        self.manual: Deck | None = None
        self._arena_listing = False  # inside a "Draft deck contains card" block
        self.draft_mode = ""  # last SetDraftMode value, e.g. DRAFTING / REDRAFTING / ACTIVE_DRAFT_DECK
        self.redraft_picks: list[str] = []
        self.pick_order: list[str] = []  # cards in the order they were picked this draft
        self._skip_listing = False
        self._finding: dict | None = None  # pending "Finding Game With Deck:" entry

    def feed_arena(self, ts: datetime, text: str) -> None:
        if m := RE_ARENA_DECK.search(text):
            if self.arena and self.arena.exact and self.arena.deck_id == m[1]:
                self._skip_listing = True  # memory already gave us the exact list
                return
            self.arena = self._arena_deck(m[1], m[2], ts)
            self._arena_listing = True
            return
        if (self._arena_listing or self._skip_listing) and (m := RE_ARENA_CARD.search(text)):
            if self._arena_listing:
                self.arena.cards.append(m[1])
            return
        self._arena_listing = self._skip_listing = False
        if m := RE_ARENA_NEW.search(text):
            self.arena = self._arena_deck(m[1], "", ts)
            self.draft_mode = "DRAFTING"
            self.pick_order = []
        elif RE_ARENA_REDRAFT.search(text):
            self.redraft_picks = []
        elif self.drafting and self.arena and (m := RE_ARENA_PICK.search(text)):
            card_id = m[1]
            if card_id.startswith("HERO_"):
                self.arena = self._arena_deck(self.arena.deck_id, card_id, ts, self.arena.cards)
            elif self.draft_mode == "REDRAFTING":
                # The full list is re-printed afterwards, so keep these apart.
                self.redraft_picks.append(card_id)
                self.pick_order.append(card_id)
            else:
                self.arena.cards.append(card_id)
                self.pick_order.append(card_id)
            self.arena.seen_at = ts
        elif m := RE_ARENA_MODE.search(text):
            self.draft_mode = m[1]
            if m[1] == "REDRAFTING":
                self.redraft_picks = []

    @property
    def drafting(self) -> bool:
        return self.draft_mode in ("DRAFTING", "REDRAFTING")

    def set_exact_arena(self, deck_id: str, hero: str, cards: list[str], ts: datetime) -> None:
        """The arena deck as read from game memory, with exact copy counts."""
        arena = self.arena
        if arena and arena.exact and arena.deck_id == deck_id and arena.cards == cards and arena.hero == hero:
            return
        self.arena = self._arena_deck(deck_id, hero, ts, cards)
        self.arena.exact = True

    def _arena_deck(self, deck_id: str, hero: str, ts: datetime, cards: list[str] | None = None) -> Deck:
        cls = self.cards.class_of(hero) if hero else None
        name = f"Arena · {CLASS_NAMES[cls]}" if cls in CLASS_NAMES else "Arena"
        return Deck(source="arena", name=name, deck_id=deck_id, hero=hero, cards=list(cards or []), seen_at=ts)

    def feed_decks(self, ts: datetime, text: str) -> None:
        text = text.strip()
        if text.startswith("Finding Game With Deck:"):
            self._finding = {}
            return
        if self._finding is None:
            return
        if m := RE_DECK_NAME.match(text):
            self._finding["name"] = m[1].strip()
        elif m := RE_DECK_ID.match(text):
            self._finding["deck_id"] = m[1]
        elif m := RE_DECKSTRING.match(text):
            try:
                deck = deck_from_code(m[1], self.cards, source="constructed", **self._finding)
            except ValueError:
                deck = None
            if deck:
                deck.seen_at = ts
                self.constructed = deck
            self._finding = None
        else:
            self._finding = None

    def deck_for(self, game_type: str, game_start: datetime, previous_start: datetime | None) -> Deck | None:
        """The deck the player queued with for a game of this type."""
        if game_type in ARENA_GAME_TYPES:
            return self.arena.copy() if self.arena and self.arena.cards else None
        if not is_supported(game_type):
            return None
        fresh = self.constructed and (previous_start is None or self.constructed.seen_at > previous_start)
        if fresh:
            return self.constructed.copy()
        return self.manual.copy() if self.manual else None
