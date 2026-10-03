"""Combines game state, deck detection and history into what the UI shows."""

from collections import Counter
from datetime import datetime

from .cards import CLASS_NAMES, CardDB
from .decks import ARENA_GAME_TYPES, Deck, DeckDetector, deck_from_code, is_supported
from .history import History
from .hsmemory import DraftState
from .logfile import LogLine
from .power import Game, Player, PowerParser
from .ratings import Ratings

HIDDEN_TYPES = {"ENCHANTMENT", "HERO_POWER", "PLAYER", "GAME", "MOVE_MINION_HOVER_TARGET", "BATTLEGROUND_SPELL"}

MODE_NAMES = {
    "GT_ARENA": "Arena", "GT_UNDERGROUND_ARENA": "Underground Arena", "GT_RANKED": "Ranked",
    "GT_CASUAL": "Casual", "GT_VS_AI": "vs AI", "GT_VS_FRIEND": "Friendly Challenge",
    "GT_TAVERNBRAWL": "Tavern Brawl", "GT_BATTLEGROUNDS": "Battlegrounds",
}


def mode_name(game_type: str) -> str:
    if game_type in MODE_NAMES:
        return MODE_NAMES[game_type]
    if game_type.startswith("GT_BATTLEGROUNDS"):
        return "Battlegrounds"
    return game_type.removeprefix("GT_").replace("_", " ").title()


class Tracker:
    def __init__(self, cards: CardDB, history: History | None = None, session: str = "",
                 ratings: Ratings | None = None):
        self.cards = cards
        self.ratings = ratings or Ratings()
        self.history = history
        self.session = session
        self.decks = DeckDetector(cards)
        self.parser = PowerParser(on_game_start=self._game_started, on_game_end=self._game_ended)
        self.deck: Deck | None = None
        self._deck_game: Game | None = None  # game self.deck was resolved for
        self._previous_start: datetime | None = None
        self.recorded: list[dict] = []
        self.memory_status: str | None = None  # None while memory reading is off
        self.memory_draft: DraftState | None = None

    # -- input ---------------------------------------------------------------

    def feed(self, line: LogLine) -> None:
        if line.kind == "Power":
            self.parser.feed(line.ts, line.text)
            game = self.parser.game
            if game is not None and self._deck_game is not game and game.game_type:
                self._resolve_deck(game)
        elif line.kind == "Arena":
            self.decks.feed_arena(line.ts, line.text)
        elif line.kind == "Decks":
            self.decks.feed_decks(line.ts, line.text)

    def set_memory_draft(self, state: DraftState | None, status: str | None) -> bool:
        """Draft screen state read from game memory; returns True if anything changed."""
        if (status, state) == (self.memory_status, self.memory_draft):
            return False
        self.memory_status, self.memory_draft = status, state
        if state is not None and state.deck_id and state.deck:
            self.decks.set_exact_arena(state.deck_id, state.hero, state.deck, datetime.now())
        return True

    @property
    def drafting(self) -> bool:
        if self.memory_status == "ok":  # memory knows whether the draft screen is open
            return self.memory_draft is not None and self.memory_draft.mode in ("DRAFTING", "REDRAFTING")
        return self.decks.drafting

    def set_manual_deck(self, code: str) -> Deck:
        """Use a pasted deck code for the current game and later constructed games."""
        deck = deck_from_code(code, self.cards)
        deck.seen_at = datetime.now()
        self.decks.manual = deck
        if self.parser.game is not None:
            self.deck = deck.copy()
        return deck

    def _resolve_deck(self, game: Game) -> None:
        self._deck_game = game
        if game.resumed_from is not None:
            return  # reconnect: keep the deck we already had
        self.deck = self.decks.deck_for(game.game_type, game.start, self._previous_start)
        self._previous_start = game.start
        if self.deck and self.deck.source == "arena" and not self.deck.exact and self.history is not None:
            # Arena.log lists each card once per slot without copy counts; top up
            # with the most copies we've actually seen in earlier games of this run.
            listed = Counter(self.deck.cards)
            for card_id, n in self.history.copies(self.deck.deck_id).items():
                if card_id in listed and n > listed[card_id]:
                    self.deck.cards += [card_id] * (n - listed[card_id])

    def _game_started(self, game: Game) -> None:
        if game.resumed_from is None:
            self.deck = None

    def _game_ended(self, game: Game) -> None:
        me, opp = game.friendly, game.opponent
        if not is_supported(game.game_type) or me is None or opp is None:
            return
        deck = self.deck
        my_hero, opp_hero = game.hero_card(me), game.hero_card(opp)
        my_cards = self._starting_deck_seen(game, me)
        record = {
            "game_key": f"{self.session}:{game.key}",
            "session": self.session,
            "started_at": game.start.isoformat(timespec="seconds"),
            "ended_at": game.end.isoformat(timespec="seconds") if game.end else None,
            "game_type": game.game_type,
            "format_type": game.format_type,
            "result": game.result(me) or None,
            "went_first": int(game.player_tag(me, "FIRST_PLAYER") == "1"),
            "turns": (game.turn + 1) // 2,
            "my_name": me.name,
            "my_hero": my_hero,
            "my_class": self._class(game, me),
            "opp_name": opp.name,
            "opp_hero": opp_hero,
            "opp_class": self._class(game, opp),
            "deck_source": deck.source if deck else None,
            "deck_id": (deck.deck_id or deck.deckstring) if deck else None,
            "deck_name": deck.name if deck else None,
            "deck_cards": deck.cards if deck else None,
            "my_cards": sorted(my_cards.elements()),
            "my_opening": sorted(
                e.known_id for e in (game.entities.get(eid) for eid in game.opening_hand.get(me.player_id, []))
                if e is not None and e.in_starting_deck and e.known_id
            ),
            "my_played": [[turn, card, created] for turn, card, created in self._plays(game, me)],
            "opp_cards": {
                "deck": sorted(self._opponent_seen(game, opp).elements()),
                "played": [[turn, card] for turn, card, _ in self._plays(game, opp)],
            },
        }
        self.recorded.append(record)
        if self.history is not None:
            self.history.add_game(record)
            if deck and deck.source == "arena" and deck.deck_id:
                self.history.learn_copies(deck.deck_id, my_cards)

    # -- derived views -------------------------------------------------------

    def _card(self, card_id: str) -> dict:
        return self.cards.info(card_id)

    def _visible(self, card_id: str) -> bool:
        card = self.cards.get(card_id)
        return card is None or card.get("type") not in HIDDEN_TYPES

    def _sorted_rows(self, rows: list[dict]) -> list[dict]:
        return sorted(rows, key=lambda r: (r["card"]["cost"] if r["card"]["cost"] is not None else 99,
                                           r["card"]["name"]))

    def _class(self, game: Game, player: Player) -> str | None:
        return game.hero_class(player) or self.cards.class_of(game.hero_card(player))

    def _starting_deck_seen(self, game: Game, player: Player) -> Counter:
        """Cards from the player's starting deck that have been revealed so far."""
        return Counter(
            e.known_id for e in game.entities.values()
            if e.owner == player.player_id and e.in_starting_deck and e.known_id
        )

    def _opponent_seen(self, game: Game, opp: Player) -> Counter:
        return self._starting_deck_seen(game, opp)

    def _plays(self, game: Game, player: Player) -> list[tuple[int, str, bool]]:
        out = []
        for play in game.plays:
            ent = game.entities.get(play.entity_id)
            if play.player_id != player.player_id or ent is None or not ent.known_id:
                continue
            if self._visible(ent.known_id):
                out.append(((play.turn + 1) // 2, ent.known_id, not ent.in_starting_deck))
        return out

    def _deck_rows(self, deck: Deck | None, drawn: Counter, extra: Counter, in_deck: int) -> dict:
        listed = Counter(deck.cards) if deck else Counter()
        rows = []
        for card_id in listed.keys() | drawn.keys():
            total = max(listed[card_id], drawn[card_id])
            row = {"card": self._card(card_id), "total": total, "left": total - drawn[card_id]}
            if deck and card_id not in listed:
                row["unlisted"] = True
            rows.append(row)
        for card_id, n in extra.items():
            rows.append({"card": self._card(card_id), "total": n, "left": n, "created": True})
        rated = deck is not None and deck.source == "arena"
        cls = self.cards.class_of(deck.hero) if deck and deck.hero else None
        for row in rows:
            row["chance"] = round(row["left"] / in_deck, 3) if in_deck else 0
            if rated:
                row["rating"] = self._rating(cls, row["card"]["id"])
        return {"rows": self._sorted_rows(rows), "in_deck": in_deck,
                "listed_left": sum(r["left"] for r in rows)}

    def _deck_info(self, deck: Deck | None) -> dict | None:
        if deck is None:
            return None
        return {"name": deck.name, "source": deck.source, "id": deck.deck_id, "size": len(deck.cards),
                "deckstring": deck.deckstring, "cls": self.cards.class_of(deck.hero) if deck.hero else None}

    def _player_info(self, game: Game, player: Player) -> dict:
        hero = game.hero_card(player)
        cls = self._class(game, player)
        return {
            "name": player.name,
            "hero": self._card(hero) if hero else None,
            "cls": cls,
            "cls_name": CLASS_NAMES.get(cls, ""),
            "hand": game.count_zone(player, "HAND"),
            "deck": game.count_zone(player, "DECK"),
            "result": game.result(player),
            "first": game.player_tag(player, "FIRST_PLAYER") == "1",
        }

    def _rating(self, cls: str | None, card_id: str) -> dict | None:
        rating = self.ratings.get(cls, card_id)
        return {"score": rating[0], "tier": rating[1]} if rating else None

    def _draft_view(self) -> dict:
        arena = self.decks.arena
        cls = self.cards.class_of(arena.hero) if arena and arena.hero else None
        picks = list(arena.cards) if arena else []
        redraft = list(self.decks.redraft_picks) if self.decks.draft_mode == "REDRAFTING" else []

        def pick(card_id: str, redrafted: bool) -> dict:
            return {"card": self._card(card_id), "rating": self._rating(cls, card_id), "redraft": redrafted}

        if arena and arena.exact:
            # Exact deck from memory: keep the logged pick order for the cards really in it
            # (the log also records legendaries that were only looked at), then the rest.
            remaining = Counter(picks)
            ordered_ids = []
            for card_id in self.decks.pick_order:
                if remaining[card_id] > 0:
                    remaining[card_id] -= 1
                    ordered_ids.append(card_id)
            # Cards never picked directly (e.g. a legendary's package) go last.
            ordered = [pick(c, False) for c in list(remaining.elements()) + ordered_ids]
        else:
            ordered = [pick(c, False) for c in picks] + [pick(c, True) for c in redraft]
        scores = [p["rating"]["score"] for p in ordered if p["rating"]]
        tierlist = sorted(
            ({"card": self._card(card_id), "score": score, "tier": tier}
             for card_id, (score, tier) in self.ratings.for_class(cls).items()),
            key=lambda r: -r["score"],
        ) if cls else []
        return {
            "cls": cls,
            "cls_name": CLASS_NAMES.get(cls, ""),
            "hero": self._card(arena.hero) if arena and arena.hero else None,
            "mode": self.decks.draft_mode,
            "picks": list(reversed(ordered)),  # newest first
            "count": len(picks),
            "average": round(sum(scores) / len(scores), 1) if scores else None,
            "tierlist": tierlist,
            "source": "HearthArena" if len(self.ratings) else None,
            "offer": self._offer(cls),
        }

    def _offer(self, cls: str | None) -> dict | None:
        """The cards currently offered on the draft screen, with ratings (memory reading only)."""
        state = self.memory_draft
        if state is None or not state.choices or state.mode not in ("DRAFTING", "REDRAFTING"):
            return None
        rated = state.slot == "CARD"
        choices = []
        for choice in state.choices:
            choices.append({
                "card": self._card(choice.card_id),
                "rating": self._rating(cls, choice.card_id) if rated else None,
                "package": [{"card": self._card(c), "rating": self._rating(cls, c) if rated else None}
                            for c in choice.package],
            })
        scores = [c["rating"]["score"] for c in choices if c["rating"]]
        for c in choices:
            c["best"] = bool(scores) and len(scores) > 1 and c["rating"] is not None \
                and c["rating"]["score"] == max(scores)
        return {"slot": state.slot, "choices": choices}

    def upcoming_deck(self) -> Deck | None:
        candidates = [d for d in (self.decks.arena, self.decks.constructed, self.decks.manual) if d and d.cards]
        return max(candidates, key=lambda d: d.seen_at) if candidates else None

    def snapshot(self) -> dict:
        game = self.parser.game
        state: dict = {"session": self.session, "status": "idle", "game": None, "deck": None,
                       "my_deck": None, "opponent": None}

        state["memory"] = {"enabled": self.memory_status is not None, "status": self.memory_status}
        if self.drafting and (game is None or game.complete or not is_supported(game.game_type)):
            state["status"] = "drafting"
            state["draft"] = self._draft_view()
            deck = self.decks.arena
            state["deck"] = self._deck_info(deck)
            if deck:
                state["my_deck"] = self._deck_rows(deck, Counter(), Counter(), len(deck.cards))
            return state

        if game is None or not game.game_type:
            deck = self.upcoming_deck()
            state["deck"] = self._deck_info(deck)
            if deck:
                state["my_deck"] = self._deck_rows(deck, Counter(), Counter(), len(deck.cards))
            return state

        me, opp = game.friendly, game.opponent
        state["game"] = {
            "mode": mode_name(game.game_type),
            "game_type": game.game_type,
            "format": game.format_type,
            "turn": (game.turn + 1) // 2,
            "start": game.start.isoformat(timespec="seconds"),
            "arena": game.game_type in ARENA_GAME_TYPES,
        }
        if not is_supported(game.game_type) or me is None or opp is None:
            state["status"] = "unsupported" if not is_supported(game.game_type) else "starting"
            return state

        state["status"] = "finished" if game.complete else "playing"
        state["game"]["me"] = self._player_info(game, me)
        state["game"]["opp"] = self._player_info(game, opp)

        mine = [e for e in game.entities.values() if e.owner == me.player_id and e.in_starting_deck]
        drawn = Counter(e.known_id for e in mine if e.zone != "DECK" and e.known_id)
        extra = Counter(
            e.known_id for e in game.entities.values()
            if e.controller == me.player_id and e.zone == "DECK" and not e.in_starting_deck and e.known_id
        )
        in_deck = game.count_zone(me, "DECK")
        state["deck"] = self._deck_info(self.deck)
        state["my_deck"] = self._deck_rows(self.deck, drawn, extra, in_deck)

        seen = self._opponent_seen(game, opp)
        state["opponent"] = {
            "seen": self._sorted_rows([{"card": self._card(c), "total": n} for c, n in seen.items()
                                       if self._visible(c)]),
            "played": [{"turn": t, "card": self._card(c), "created": created}
                       for t, c, created in self._plays(game, opp)],
        }
        return state
