"""Rebuilds game state from the GameState lines of Power.log.

Only GameState.* lines are used; PowerTaskList.* repeats the same information
later (synchronised with animations) and would double-count.
"""

import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable

POWER_PREFIX = "GameState.DebugPrintPower() - "
GAME_PREFIX = "GameState.DebugPrintGame() - "

RE_GAME_ENTITY = re.compile(r"GameEntity EntityID=(\d+)")
RE_PLAYER = re.compile(r"Player EntityID=(\d+) PlayerID=(\d+) GameAccountId=\[hi=(\d+) lo=(\d+)\]")
RE_FULL_CREATE = re.compile(r"FULL_ENTITY - Creating ID=(\d+) CardID=(\S*)")
RE_FULL_UPDATE = re.compile(r"FULL_ENTITY - Updating (.+) CardID=(\S*)$")
RE_SHOW = re.compile(r"SHOW_ENTITY - Updating Entity=(.+) CardID=(\S*)$")
RE_CHANGE = re.compile(r"CHANGE_ENTITY - Updating Entity=(.+) CardID=(\S*)$")
RE_HIDE = re.compile(r"HIDE_ENTITY - Entity=(.+) tag=(\S+) value=(\S*)")
RE_TAG_CHANGE = re.compile(r"TAG_CHANGE Entity=(.+) tag=(\S+) value=(\S*)")
RE_TAG = re.compile(r"tag=(\S+) value=(\S*)$")
RE_BRACKET_ID = re.compile(r" id=(\d+) zone=")
RE_PLAYER_NAME = re.compile(r"PlayerID=(\d+), PlayerName=(.*)$")
RE_GAME_INFO = re.compile(r"(GameType|FormatType|BuildNumber|ScenarioID)=(\S+)")

UNKNOWN_PLAYER = "UNKNOWN HUMAN PLAYER"
SETUP_STEPS = {"", "INVALID", "BEGIN_FIRST", "BEGIN_SHUFFLE", "BEGIN_DRAW", "BEGIN_MULLIGAN"}
FINAL_PLAYSTATES = {"WON", "LOST", "TIED"}


@dataclass
class Entity:
    id: int
    card_id: str = ""
    first_card_id: str = ""  # card the entity was first revealed as (survives transforms)
    tags: dict[str, str] = field(default_factory=dict)
    in_starting_deck: bool | None = False  # None until ZONE/CONTROLLER are known
    owner: int = 0  # controller when created

    @property
    def zone(self) -> str:
        return self.tags.get("ZONE", "")

    @property
    def controller(self) -> int:
        return int(self.tags.get("CONTROLLER", 0) or 0)

    @property
    def known_id(self) -> str:
        return self.first_card_id or self.card_id

    def reveal(self, card_id: str) -> None:
        if card_id:
            self.card_id = card_id
            if not self.first_card_id:
                self.first_card_id = card_id


@dataclass
class Player:
    entity_id: int
    player_id: int
    account: str
    name: str = ""


@dataclass
class Play:
    turn: int
    entity_id: int
    player_id: int


@dataclass
class Game:
    start: datetime
    entities: dict[int, Entity] = field(default_factory=dict)
    players: dict[int, Player] = field(default_factory=dict)  # by player_id
    game_entity_id: int = 1
    game_type: str = ""
    format_type: str = ""
    build: str = ""
    scenario_id: str = ""
    setup: bool = True  # before mulligan finished
    friendly_id: int | None = None
    plays: list[Play] = field(default_factory=list)
    opening_hand: dict[int, list[int]] = field(default_factory=dict)  # player_id -> entity ids after mulligan
    end: datetime | None = None
    resumed_from: "Game | None" = None

    @property
    def key(self) -> str:
        return self.resumed_from.key if self.resumed_from else self.start.isoformat()

    @property
    def complete(self) -> bool:
        return self.end is not None

    @property
    def turn(self) -> int:
        game = self.entities.get(self.game_entity_id)
        return int(game.tags.get("TURN", 0)) if game else 0

    @property
    def friendly(self) -> Player | None:
        return self.players.get(self.friendly_id) if self.friendly_id is not None else None

    @property
    def opponent(self) -> Player | None:
        me = self.friendly
        if me is None:
            return None
        others = [p for p in self.players.values() if p is not me]
        return others[0] if len(others) == 1 else None

    def player_tag(self, player: Player, tag: str) -> str:
        ent = self.entities.get(player.entity_id)
        return ent.tags.get(tag, "") if ent else ""

    def hero_card(self, player: Player) -> str:
        hero = self.entities.get(int(self.player_tag(player, "HERO_ENTITY") or 0))
        return hero.known_id if hero else ""

    def hero_class(self, player: Player) -> str:
        hero = self.entities.get(int(self.player_tag(player, "HERO_ENTITY") or 0))
        cls = hero.tags.get("CLASS", "") if hero else ""
        return "" if cls in ("", "INVALID", "NEUTRAL") else cls

    def result(self, player: Player) -> str:
        state = self.player_tag(player, "PLAYSTATE")
        return state if state in FINAL_PLAYSTATES else ""

    def count_zone(self, player: Player, zone: str) -> int:
        return sum(1 for e in self.entities.values() if e.controller == player.player_id and e.zone == zone)

    def player_for_entity(self, entity_id: int) -> Player | None:
        return next((p for p in self.players.values() if p.entity_id == entity_id), None)


class PowerParser:
    def __init__(
        self,
        on_game_start: Callable[[Game], None] | None = None,
        on_game_end: Callable[[Game], None] | None = None,
    ):
        self.game: Game | None = None
        self.on_game_start = on_game_start or (lambda g: None)
        self.on_game_end = on_game_end or (lambda g: None)
        self._current: Entity | None = None  # target of indented tag=... lines
        self._depth = 0
        self._reset_depth: int | None = None
        self._reset_seen: set[int] = set()
        self._ts = datetime.min

    def feed(self, ts: datetime, text: str) -> None:
        self._ts = ts
        if text.startswith(POWER_PREFIX):
            self._power(text[len(POWER_PREFIX):].strip())
        elif text.startswith(GAME_PREFIX) and self.game:
            self._game_info(text[len(GAME_PREFIX):].strip())

    # -- line handlers -------------------------------------------------------

    def _game_info(self, payload: str) -> None:
        if m := RE_PLAYER_NAME.match(payload):
            player = self.game.players.get(int(m[1]))
            if player:
                player.name = m[2].strip()
            # The opponent's name is hidden at game start, ours never is.
            unknown = [p for p in self.game.players.values() if p.name == UNKNOWN_PLAYER]
            named = [p for p in self.game.players.values() if p.name and p.name != UNKNOWN_PLAYER]
            if self.game.friendly_id is None and len(unknown) == 1 and len(named) == 1:
                self.game.friendly_id = named[0].player_id
        elif m := RE_GAME_INFO.match(payload):
            attr = {"GameType": "game_type", "FormatType": "format_type",
                    "BuildNumber": "build", "ScenarioID": "scenario_id"}[m[1]]
            setattr(self.game, attr, m[2])

    def _power(self, payload: str) -> None:
        if payload == "CREATE_GAME":
            self._create_game()
            return
        game = self.game
        if game is None:
            return

        if m := RE_TAG.match(payload):
            if self._current is not None:
                self._set_tag(self._current, m[1], m[2])
            return
        if payload.startswith("TAG_CHANGE"):
            self._current = None
            if (m := RE_TAG_CHANGE.match(payload)) and (ent := self._entity(m[1])):
                self._set_tag(ent, m[2], m[3])
            return

        self._current = None
        if m := RE_FULL_CREATE.match(payload):
            self._current = self._create_entity(int(m[1]), m[2])
        elif m := RE_SHOW.match(payload):
            if ent := self._entity(m[1]):
                ent.reveal(m[2])
                self._current = ent
                if game.friendly_id is None and game.setup and ent.controller in game.players:
                    # Only our own cards get revealed before the mulligan is done.
                    game.friendly_id = ent.controller
        elif m := RE_FULL_UPDATE.match(payload):
            if ent := self._entity(m[1]):
                ent.reveal(m[2])
                self._current = ent
                if self._reset_depth is not None:
                    self._reset_seen.add(ent.id)
        elif m := RE_CHANGE.match(payload):
            if ent := self._entity(m[1]):
                ent.card_id = m[2] or ent.card_id
                self._current = ent
        elif m := RE_HIDE.match(payload):
            if ent := self._entity(m[1]):
                self._set_tag(ent, m[2], m[3])
        elif m := RE_GAME_ENTITY.match(payload):
            game.game_entity_id = int(m[1])
            self._current = self._create_entity(int(m[1]), "")
        elif m := RE_PLAYER.match(payload):
            eid, pid = int(m[1]), int(m[2])
            game.players[pid] = Player(entity_id=eid, player_id=pid, account=f"{m[3]}:{m[4]}")
            self._current = self._create_entity(eid, "")
        elif payload.startswith("BLOCK_START"):
            self._depth += 1
        elif payload.startswith("BLOCK_END"):
            self._depth -= 1
            if self._reset_depth is not None and self._depth < self._reset_depth:
                self._finish_reset()
        elif payload == "RESET_GAME":
            # A "rewind" effect: the server re-sends every entity that still exists.
            self._reset_depth = self._depth
            self._reset_seen = set()

    # -- state changes -------------------------------------------------------

    def _create_game(self) -> None:
        previous = self.game
        self.game = Game(start=self._ts)
        if previous is not None and not previous.complete:
            # Reconnect: Hearthstone replays the full state as a new game.
            self.game.resumed_from = previous
        self._current = None
        self._depth = 0
        self._reset_depth = None
        self.on_game_start(self.game)

    def _create_entity(self, eid: int, card_id: str) -> Entity:
        ent = Entity(id=eid)
        ent.reveal(card_id)
        old = self.game.resumed_from.entities.get(eid) if self.game.resumed_from else None
        if old is not None:
            ent.first_card_id = old.first_card_id or ent.first_card_id
            ent.in_starting_deck = old.in_starting_deck
            ent.owner = old.owner
        else:
            ent.in_starting_deck = None  # decided once its ZONE/CONTROLLER tags arrive
        self.game.entities[eid] = ent
        return ent

    def _set_tag(self, ent: Entity, tag: str, value: str) -> None:
        game = self.game
        old = ent.tags.get(tag)
        ent.tags[tag] = value

        if ent.in_starting_deck is None and tag in ("ZONE", "CONTROLLER"):
            if "ZONE" in ent.tags and "CONTROLLER" in ent.tags:
                ent.in_starting_deck = game.setup and ent.zone == "DECK"
                ent.owner = ent.controller

        if tag == "ZONE" and old == "HAND" and value in ("PLAY", "SECRET") and self._reset_depth is None:
            game.plays.append(Play(turn=game.turn, entity_id=ent.id, player_id=ent.controller))
        elif ent.id == game.game_entity_id:
            if tag == "STEP" and value not in SETUP_STEPS:
                if game.setup and game.resumed_from is None:
                    for pid in game.players:
                        game.opening_hand[pid] = [e.id for e in game.entities.values()
                                                  if e.controller == pid and e.zone == "HAND"]
                game.setup = False
            elif tag == "STATE" and value == "COMPLETE" and not game.complete:
                game.end = self._ts
                self.on_game_end(game)

    def _finish_reset(self) -> None:
        game = self.game
        for ent in game.entities.values():
            if ent.id not in self._reset_seen and ent.id != game.game_entity_id \
                    and game.player_for_entity(ent.id) is None:
                ent.tags["ZONE"] = "REMOVEDFROMGAME"
        self._reset_depth = None
        self._reset_seen = set()

    def _entity(self, ref: str) -> Entity | None:
        game = self.game
        ref = ref.strip()
        if ref.isdigit():
            return game.entities.get(int(ref))
        if ref.startswith("["):
            m = RE_BRACKET_ID.search(ref)
            return game.entities.get(int(m[1])) if m else None
        if ref == "GameEntity":
            return game.entities.get(game.game_entity_id)
        player = self._player_by_name(ref)
        return game.entities.get(player.entity_id) if player else None

    def _player_by_name(self, name: str) -> Player | None:
        players = list(self.game.players.values())
        for p in players:
            if p.name == name:
                return p
        short = name.split("#")[0]
        for p in players:
            if p.name and p.name != UNKNOWN_PLAYER and p.name.split("#")[0] == short:
                return p
        # The opponent's real name shows up mid-game; claim the anonymous player.
        unnamed = [p for p in players if p.name in ("", UNKNOWN_PLAYER)]
        if len(unnamed) == 1:
            unnamed[0].name = name
            return unnamed[0]
        return None
