"""Combines game state, deck detection and history into what the UI shows."""

from collections import Counter
from datetime import datetime

from . import stats
from .cards import CLASS_NAMES, CardDB
from .classstats import ClassStats
from .decks import ARENA_GAME_TYPES, Deck, DeckDetector, deck_from_code, is_supported
from .history import History
from .hsmemory import DraftState
from .logfile import LogLine
from .power import Block, Game, Player, PowerParser
from .ratings import Ratings
from .secrets import SecretInfo, SecretWatcher, secrets_in_play

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
                 ratings: Ratings | None = None, class_stats: ClassStats | None = None):
        self.cards = cards
        self.ratings = ratings or Ratings()
        self.class_stats = class_stats or ClassStats()
        self.history = history
        self.session = session
        self.decks = DeckDetector(cards)
        self.parser = PowerParser(on_game_start=self._game_started, on_game_end=self._game_ended,
                                  on_block_start=self._block_start, on_block_end=self._block_end,
                                  on_step=self._step)
        self.secrets = SecretWatcher()  # the opponent's secrets in the current game
        self._odds: tuple[tuple, dict] | None = None  # (what it was computed for, result)
        self.deck: Deck | None = None
        self._deck_game: Game | None = None  # game self.deck was resolved for
        self._previous_start: datetime | None = None
        self.recorded: list[dict] = []
        self.memory_status: str | None = None  # None while memory reading is off
        self.memory_draft: DraftState | None = None
        self._offered: dict[str, dict[str, list[str]]] = {}  # deck_id -> {legendary: package cards}

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
        if state is not None and state.mode in ("DRAFTING", "REDRAFTING"):
            self._remember_packages(state)
        if state is not None and state.deck_id and state.deck:
            self.decks.set_exact_arena(state.deck_id, state.hero, state.deck, datetime.now())
        return True

    def _remember_packages(self, state: DraftState) -> None:
        """Note the package offered with each legendary; HearthArena rates only the legendary."""
        arena = self.decks.arena
        deck_id = state.deck_id or (arena.deck_id if arena else "")
        if not deck_id:
            return
        known = self._offered_packages(deck_id)
        for choice in state.choices:
            if choice.package and known.get(choice.card_id) != choice.package:
                known[choice.card_id] = list(choice.package)
                if self.history is not None:
                    self.history.add_package(deck_id, choice.card_id, choice.package)

    def _offered_packages(self, deck_id: str) -> dict[str, list[str]]:
        if deck_id not in self._offered:
            self._offered[deck_id] = self.history.packages(deck_id) if self.history is not None else {}
        return self._offered[deck_id]

    def _packages(self, deck: Deck | None) -> dict[str, str]:
        """Cards of an arena deck that came bundled with one of its legendaries -> that legendary."""
        if deck is None or not deck.deck_id:
            return {}
        out: dict[str, str] = {}
        for legendary, cards in self._offered_packages(deck.deck_id).items():
            if legendary in deck.cards:
                for card_id in cards:
                    if card_id in deck.cards:
                        out.setdefault(card_id, legendary)
        return out

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
            self.secrets = SecretWatcher()

    def _sides(self, game: Game) -> tuple[Player, Player] | None:
        """(opponent, us): the secrets' owner and the player whose actions might trigger them."""
        me, opp = game.friendly, game.opponent
        return (opp, me) if me is not None and opp is not None else None

    def _block_start(self, game: Game, block: Block) -> None:
        if sides := self._sides(game):
            self.secrets.block_start(game, block, *sides)

    def _block_end(self, game: Game, block: Block) -> None:
        if sides := self._sides(game):
            self.secrets.block_end(game, block, *sides)

    def _step(self, game: Game, step: str) -> None:
        if sides := self._sides(game):
            self.secrets.step(game, step, *sides)

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
        packages = self._packages(deck) if rated else {}
        for row in rows:
            row["chance"] = round(row["left"] / in_deck, 3) if in_deck else 0
            if rated:
                row["rating"] = self._rating(cls, row["card"]["id"])
                row["package"] = self._package(packages, row["card"]["id"])
        return {"rows": self._sorted_rows(rows), "in_deck": in_deck,
                "listed_left": sum(r["left"] for r in rows), "summary": self._deck_summary(rows)}

    def _deck_summary(self, rows: list[dict]) -> dict:
        """What's left in the deck: minion stats, card types and the mana curve (7 = 7+)."""
        types, curve, minions = Counter(), [0] * 8, Counter()
        attack = health = 0
        for row in rows:
            card, left = self.cards.get(row["card"]["id"]), row["left"]
            if card is None or not left:
                continue
            types[card.get("type", "").lower()] += left
            if card.get("cost") is not None:
                curve[min(card["cost"], 7)] += left
            if card.get("type") == "MINION":
                attack += left * card.get("attack", 0)
                health += left * card.get("health", 0)
                minions[card["id"]] += left
        return {"types": dict(types), "curve": curve, "attack": attack, "health": health,
                "minion_types": self._minion_types(minions)}

    def _minion_types(self, minions: Counter) -> dict[str, int]:
        """Minions per type, most first. Dual-type minions count for both types, and minions of
        every type (Amalgams) for each type there is, or as "ALL" when there's no other."""
        types = Counter()
        for card_id, n in minions.items():
            for race in (self.cards.get(card_id) or {}).get("races", ()):
                types[race] += n
        every = types.pop("ALL", 0)
        if not types:
            return {"ALL": every} if every else {}
        return {race: n + every for race, n in types.most_common()}

    def _secret_view(self, game: Game, opp: Player) -> list[dict]:
        """The opponent's hidden secrets, each with the cards it could be and the ones ruled out."""
        played = {p.entity_id: (p.turn + 1) // 2 for p in game.plays}
        out = []
        for sid in secrets_in_play(game, opp):
            ent = game.entities[sid]
            if ent.card_id:
                continue  # revealed
            cls = ent.tags.get("CLASS")
            if cls not in CLASS_NAMES or cls == "NEUTRAL":
                cls = self._class(game, opp)
            groups = self.cards.secrets(cls) if cls else []
            if game.game_type in ARENA_GAME_TYPES and ent.in_starting_deck:
                # Only cards in the arena pool, which HearthArena's tier list covers.
                groups = [ids for ids in groups if any(self.ratings.get(cls, c) for c in ids)] or groups
            info = self.secrets.secrets.get(sid) or SecretInfo()
            options = []
            for ids in groups:
                why = next((reason for c in ids if (reason := info.ruled_out(c))), None)
                options.append({"card": self._card(ids[0]), "ruled_out": why})
            options.sort(key=lambda o: o["ruled_out"] is not None)
            out.append({"id": sid, "cls": cls, "cls_name": CLASS_NAMES.get(cls, ""),
                        "turn": played.get(sid), "options": options})
        return out

    def _odds_view(self, game: Game, me: Player, opp: Player) -> dict | None:
        """Our win rate in earlier games like this one: same game type, classes and turn order."""
        my_cls, opp_cls = self._class(game, me), self._class(game, opp)
        if self.history is None or not my_cls or not opp_cls:
            return None
        first = game.player_tag(me, "FIRST_PLAYER") == "1"
        key = (game.key, my_cls, opp_cls, first)
        if self._odds is None or self._odds[0] != key:  # once per game, not on every update
            this_game = f"{self.session}:{game.key}"
            games = [g for g in stats.filter_games(self.history.games(), game.game_type)
                     if g["game_key"] != this_game]
            self._odds = key, {**stats.odds(games, my_cls, opp_cls, first), "first": first,
                               "mode": mode_name(game.game_type),
                               "my_cls_name": CLASS_NAMES.get(my_cls, ""),
                               "opp_cls_name": CLASS_NAMES.get(opp_cls, "")}
        return self._odds[1]

    def _hand_view(self, game: Game, opp: Player) -> list[dict]:
        """The opponent's hand in order: when each card arrived, how, and from what, if known."""
        cards = sorted((e for e in game.entities.values() if e.controller == opp.player_id and e.zone == "HAND"),
                       key=lambda e: int(e.tags.get("ZONE_POSITION") or 0))
        out = []
        for ent in cards:
            arrival = ent.arrival
            how, by = "drawn", None
            if arrival is not None:
                created = arrival.origin in ("", "SETASIDE", "REMOVEDFROMGAME", "INVALID")
                if arrival.setup:
                    how = "start" if created else arrival.setup
                elif created:
                    how = "created"
                    # DISPLAYED_CREATOR is what the game shows; CREATOR is only visible for revealed cards.
                    by = int(ent.tags.get("DISPLAYED_CREATOR") or ent.tags.get("CREATOR") or 0) or arrival.source
                else:
                    how = "drawn" if arrival.origin == "DECK" else "returned"
                    by = arrival.source
            creator = game.entities.get(by) if by else None
            out.append({
                "turn": (arrival.turn + 1) // 2 if arrival and not arrival.setup else None,
                "how": how,
                "card": self._card(ent.card_id) if ent.card_id else None,
                # A hidden creator (a card still in their hand) is reported as unknown.
                "by": (self._card(creator.known_id) if creator.known_id else {"id": None, "name": "an unknown card"})
                      if creator is not None and creator.id != game.game_entity_id else None,
            })
        return out

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
            "spells": self._spells(game, player),
            "minions": self._minions(game, player),
        }

    def _played_cards(self, game: Game, player: Player, card_type: str) -> list[tuple[int, dict]]:
        """(game turn, card) for each card of this type the player has played."""
        out = []
        for play in game.plays:
            ent = game.entities.get(play.entity_id)
            if play.player_id != player.player_id or ent is None or not ent.known_id:
                continue
            card = self.cards.get(ent.known_id)
            if card is not None and card.get("type") == card_type:
                out.append((play.turn, card))
        return out

    def _spells(self, game: Game, player: Player) -> dict:
        """Spells the player has cast this game, per spell school, most cast first."""
        played = [card for _, card in self._played_cards(game, player, "SPELL")]
        schools = Counter(card["spellSchool"] for card in played if card.get("spellSchool"))
        # The game's own count also includes spells cast by card effects.
        counted = game.player_tag(player, "NUM_SPELLS_PLAYED_THIS_GAME")
        return {"cast": max(int(counted or 0), len(played)),
                "schools": [{"school": school, "count": n} for school, n in schools.most_common()]}

    def _minions(self, game: Game, player: Player) -> dict:
        """Minions the player has played this game, per minion type, and their run of Elementals."""
        played = self._played_cards(game, player, "MINION")
        return {"played": len(played),
                "types": self._minion_types(Counter(card["id"] for _, card in played)),
                "elementals": self._elementals(game, player, played)}

    def _elementals(self, game: Game, player: Player, played: list[tuple[int, dict]]) -> dict | None:
        """Whether the player played an Elemental this turn and last turn, and in how many of
        their turns in a row; None until they've played one. Game turns alternate between players."""
        turns = {turn for turn, card in played if {"ELEMENTAL", "ALL"} & set(card.get("races", ()))}
        if not turns:
            return None
        first = game.player_tag(player, "FIRST_PLAYER") == "1"
        theirs = game.turn % 2 == (1 if first else 0)
        current = game.turn if theirs else None
        last = game.turn - 2 if theirs else game.turn - 1
        streak, turn = 0, current if current in turns else last
        while turn in turns:
            streak, turn = streak + 1, turn - 2
        return {"this_turn": current in turns, "last_turn": last in turns, "streak": streak}

    def _rating(self, cls: str | None, card_id: str) -> dict | None:
        rating = self.ratings.get(cls, card_id)
        return {"score": rating[0], "tier": rating[1]} if rating else None

    def _package(self, packages: dict[str, str], card_id: str) -> dict | None:
        """{"legendary": card} for a card that came bundled with a legendary pick."""
        return {"legendary": self._card(packages[card_id])} if card_id in packages else None

    def _draft_view(self) -> dict:
        arena = self.decks.arena
        cls = self.cards.class_of(arena.hero) if arena and arena.hero else None
        picks = list(arena.cards) if arena else []
        redraft = list(self.decks.redraft_picks) if self.decks.draft_mode == "REDRAFTING" else []
        packages = self._packages(arena)

        def pick(card_id: str, redrafted: bool) -> dict:
            return {"card": self._card(card_id), "rating": self._rating(cls, card_id), "redraft": redrafted,
                    "package": None if redrafted else self._package(packages, card_id)}

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
            ordered += [pick(c, True) for c in redraft]
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
        runs = self._runs_by_class(state.underground) if state.slot == "HERO" else {}
        choices = []
        for choice in state.choices:
            choices.append({
                "card": self._card(choice.card_id),
                "rating": self._rating(cls, choice.card_id) if rated else None,
                "package": [{"card": self._card(c), "rating": self._rating(cls, c) if rated else None}
                            for c in choice.package],
            })
            if state.slot == "HERO":
                hero_cls = self.cards.class_of(choice.card_id)
                choices[-1]["class"] = {"cls_name": CLASS_NAMES.get(hero_cls, ""),
                                        "global": self.class_stats.get(state.underground, hero_cls),
                                        "mine": runs.get(hero_cls)}
        scores = [c["rating"]["score"] for c in choices if c["rating"]]
        for c in choices:
            c["best"] = bool(scores) and len(scores) > 1 and c["rating"] is not None \
                and c["rating"]["score"] == max(scores)
        return {"slot": state.slot, "choices": choices}

    def _runs_by_class(self, underground: bool) -> dict[str, dict]:
        """Our earlier runs in this arena mode, per class: {class: {"runs", "avg_wins", "best", ...}}."""
        if self.history is None:
            return {}
        game_type = "GT_UNDERGROUND_ARENA" if underground else "GT_ARENA"
        runs = stats.arena_runs(stats.filter_games(self.history.games(), game_type))
        return {r["my_class"]: r for r in runs["by_class"]}

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
        state["odds"] = self._odds_view(game, me, opp)

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
            "secrets": self._secret_view(game, opp),
            "hand": self._hand_view(game, opp),
        }
        return state
