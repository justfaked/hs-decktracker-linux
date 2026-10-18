"""Narrows down which secret the opponent is hiding, from the events it didn't trigger on.

Each of our actions is watched as a window: a top-level block (a card played, an
attack, deaths) or the start of their turn / the end of ours. A secret that is
still hidden afterwards can't be any secret that action would have set off. If a
secret fires inside the window we learn nothing about the others, since its effect
may have stopped them (a countered spell, a killed attacker).

Secrets only trigger on their owner's opponent's turn, i.e. on ours, apart from
the few that trigger when their owner's turn starts.
"""

import re
from dataclasses import dataclass, field

from .power import Block, Game, Player

# Events, seen from the secret's owner: "you" in the reasons is us.
SPELL = "spell"
SPELL_ON_MINION = "spell_on_minion"
MINION = "minion"
HERO_POWER = "hero_power"
FRESH_CARD = "fresh_card"  # a card played on the turn it entered our hand
THIRD_CARD = "third_card"
ATTACK = "attack"  # we attack anything
MINION_ATTACKS = "minion_attacks"
HERO_ATTACKED = "hero_attacked"
MINION_ATTACKS_HERO = "minion_attacks_hero"
MINION_ATTACKED = "minion_attacked"
MINION_DIES = "minion_dies"  # one of their minions dies on our turn
TURN_START = "turn_start"  # their turn starts
TURN_END = "turn_end"  # our turn ends

EVENT_TEXT = {
    SPELL: "you cast a spell",
    SPELL_ON_MINION: "you cast a spell on a minion",
    MINION: "you played a minion",
    HERO_POWER: "you used your hero power",
    FRESH_CARD: "you played a card the turn you got it",
    THIRD_CARD: "you played a third card in a turn",
    ATTACK: "you attacked",
    MINION_ATTACKS: "your minion attacked",
    HERO_ATTACKED: "you attacked their hero",
    MINION_ATTACKS_HERO: "your minion attacked their hero",
    MINION_ATTACKED: "you attacked their minion",
    MINION_DIES: "their minion died",
    TURN_START: "their turn started",
    TURN_END: "your turn ended",
}

# What a secret needs to be able to trigger at all. Checked before and after the event.
ROOM = "room"  # their board isn't full
HAND_ROOM = "hand_room"  # their hand isn't full
THEIR_MINION = "their_minion"
THEIR_TWO = "their_two"
OUR_MINION = "our_minion"
OUR_THREE = "our_three"


@dataclass(frozen=True)
class Rule:
    event: str
    needs: frozenset[str] = frozenset()


def rule(event: str, *needs: str) -> Rule:
    return Rule(event, frozenset(needs))


# By original card ID (Core and Classic reprints share it). Secrets missing here,
# usually because they trigger only under conditions the log doesn't show, are
# never ruled out.
RULES = {
    # Hunter
    "TTN_504": rule(MINION_ATTACKED),  # Bait and Switch
    "AT_060": rule(HERO_ATTACKED, ROOM),  # Bear Trap
    "KAR_004": rule(SPELL, ROOM),  # Cat Trick
    "LOE_021": rule(HERO_POWER),  # Dart Trap
    "TSC_929": rule(MINION_DIES),  # Emergency Maneuvers
    "EX1_610": rule(HERO_ATTACKED),  # Explosive Trap
    "EX1_611": rule(MINION_ATTACKS),  # Freezing Trap
    "AV_226": rule(SPELL),  # Ice Trap
    "MAW_010": rule(THIRD_CARD),  # Motion Denied
    "DMF_123": rule(TURN_START, THEIR_TWO, ROOM),  # Open the Cages
    "BT_203": rule(MINION_ATTACKED, ROOM),  # Pack Tactics
    "ULD_152": rule(SPELL, OUR_MINION),  # Pressure Plate
    "GIL_577": rule(THIRD_CARD, ROOM),  # Rat Trap
    "EX1_554": rule(MINION_ATTACKED, ROOM),  # Snake Trap
    "EX1_609": rule(MINION),  # Snipe
    "WON_018": rule(MINION),  # Snipe
    "ICC_200": rule(MINION_ATTACKED, ROOM),  # Venomstrike Trap
    "LOOT_079": rule(HERO_ATTACKED, ROOM),  # Wandering Monster
    "NX2_013": rule(MINION, ROOM),  # ZOMBEEEES!!!
    # Mage
    "WW_422": rule(FRESH_CARD, HAND_ROOM),  # Azerite Vein
    "EX1_287": rule(SPELL),  # Counterspell
    "FP1_018": rule(MINION_DIES, HAND_ROOM),  # Duplicate
    "AT_002": rule(MINION_DIES),  # Effigy
    "LOOT_101": rule(MINION),  # Explosive Runes
    "ULD_239": rule(MINION_ATTACKS_HERO),  # Flame Ward
    "END_024": rule(TURN_END, OUR_MINION),  # Flames of Infinity
    "ICC_082": rule(MINION, HAND_ROOM),  # Frozen Clone
    "EX1_289": rule(HERO_ATTACKED),  # Ice Barrier
    "UNG_024": rule(SPELL, HAND_ROOM),  # Mana Bind
    "EX1_294": rule(MINION, ROOM),  # Mirror Entity
    "JAIL_315": rule(MINION_ATTACKS),  # Mystic Misdirection
    "BT_003": rule(SPELL, ROOM),  # Netherwind Portal
    "BAR_812": rule(MINION_ATTACKED, ROOM),  # Oasis Ally
    "MAW_006": rule(MINION),  # Objection!
    "CFM_620": rule(MINION),  # Potion of Polymorph
    "tt_010": rule(SPELL_ON_MINION, ROOM),  # Spellbender
    "TRL_400": rule(MINION_ATTACKED, ROOM),  # Splitting Image
    "DEEP_000": rule(TURN_START, THEIR_MINION, ROOM),  # Summoning Ward
    "EX1_594": rule(MINION_ATTACKS_HERO),  # Vaporize
    "REV_516": rule(MINION_ATTACKS_HERO, ROOM),  # Vengeful Visage
    # Paladin
    "BOT_908": rule(MINION_ATTACKED),  # Autodefense Matrix
    "FP1_020": rule(MINION_DIES, THEIR_MINION),  # Avenge
    "AT_073": rule(TURN_START, THEIR_MINION),  # Competitive Spirit
    "BAR_550": rule(THIRD_CARD, ROOM),  # Galloping Savior
    "CFM_800": rule(MINION_DIES, HAND_ROOM),  # Getaway Kodo
    "GIL_903": rule(THIRD_CARD),  # Hidden Wisdom
    "WC_033": rule(MINION_ATTACKS),  # Judgment of Justice
    "DAL_570": rule(SPELL, THEIR_MINION),  # Never Surrender!
    "EX1_130": rule(ATTACK, ROOM),  # Noble Sacrifice
    "DMF_236": rule(SPELL),  # Oh My Yogg!
    "EX1_136": rule(MINION_DIES),  # Redemption
    "EX1_379": rule(MINION),  # Repentance
    "LOE_027": rule(MINION, OUR_THREE),  # Sacred Trial
    # Rogue
    "BT_707": rule(MINION, ROOM),  # Ambush
    "BT_042": rule(MINION_ATTACKED),  # Bamboozle
    "LOOT_204": rule(MINION_DIES, HAND_ROOM),  # Cheat Death
    "BT_709": rule(SPELL),  # Dirty Tricks
    "REV_828": rule(MINION),  # Kidnap
    "MAW_018": rule(TURN_START),  # Perjury
    "DMF_513": rule(MINION_ATTACKS_HERO, ROOM),  # Shadow Clone
    "REV_827": rule(SPELL, ROOM),  # Sticky Situation
}

RE_REPRINT = re.compile(r"^(core|van)_", re.IGNORECASE)


def base_id(card_id: str) -> str:
    """CORE_EX1_287 / VAN_EX1_287 -> EX1_287."""
    return RE_REPRINT.sub("", card_id)


@dataclass
class Survived:
    event: str
    facts: frozenset[str]  # conditions that held before and after
    turn: int


@dataclass
class SecretInfo:
    survived: list[Survived] = field(default_factory=list)
    not_cards: set[str] = field(default_factory=set)  # base IDs of secrets revealed while this one was in play

    def ruled_out(self, card_id: str) -> str | None:
        """Why this secret can't be card_id, or None if it still might be."""
        if base_id(card_id) in self.not_cards:
            return "Already revealed: two copies of a secret can't be in play together"
        r = RULES.get(base_id(card_id))
        if r is None:
            return None
        for s in self.survived:
            if s.event == r.event and r.needs <= s.facts:
                return f"Would have triggered when {EVENT_TEXT[s.event]} (turn {s.turn})"
        return None


@dataclass
class Window:
    secrets: list[int]  # owner's secrets in play when it opened
    hidden: set[int]  # ...and which of them were still hidden
    facts: frozenset[str]
    our_turn: bool
    events: set[str] = field(default_factory=set)
    their_minions: set[int] = field(default_factory=set)
    cards_played: int = 0
    played_minion: int | None = None


def secrets_in_play(game: Game, owner: Player) -> list[int]:
    return sorted(e.id for e in game.entities.values()
                  if e.controller == owner.player_id and e.zone == "SECRET" and e.tags.get("SECRET") == "1")


def minions(game: Game, player: Player) -> set[int]:
    return {e.id for e in game.entities.values()
            if e.controller == player.player_id and e.zone == "PLAY" and e.tags.get("CARDTYPE") == "MINION"}


def facts(game: Game, owner: Player, enemy: Player) -> frozenset[str]:
    theirs, ours = len(minions(game, owner)), len(minions(game, enemy))
    checks = {
        ROOM: theirs < 7, HAND_ROOM: game.count_zone(owner, "HAND") < 10,
        THEIR_MINION: theirs >= 1, THEIR_TWO: theirs >= 2, OUR_MINION: ours >= 1, OUR_THREE: ours >= 3,
    }
    return frozenset(name for name, ok in checks.items() if ok)


def is_turn(game: Game, player: Player) -> bool:
    return game.player_tag(player, "CURRENT_PLAYER") == "1"


def cards_played(game: Game, player: Player) -> int:
    return int(game.player_tag(player, "NUM_CARDS_PLAYED_THIS_TURN") or 0)


class SecretWatcher:
    """Follows one game, recording for each of `owner`'s secrets what it survived."""

    def __init__(self):
        self.secrets: dict[int, SecretInfo] = {}  # by entity ID
        self._block: Window | None = None
        self._step: Window | None = None
        self._hand_at_turn_start: set[int] = set()

    def info(self, entity_id: int) -> SecretInfo:
        return self.secrets.setdefault(entity_id, SecretInfo())

    # -- parser events ---------------------------------------------------------

    def block_start(self, game: Game, block: Block, owner: Player, enemy: Player) -> None:
        w = self._block = self._open(game, owner, enemy)
        if not w.our_turn:
            return
        ent = game.entities.get(block.entity_id) if block.entity_id else None
        target = game.entities.get(block.target_id) if block.target_id else None
        if ent is None or ent.controller != enemy.player_id:
            return
        kind = ent.tags.get("CARDTYPE")
        if block.type == "PLAY":
            if kind == "HERO_POWER":
                w.events.add(HERO_POWER)
                return
            if kind == "MINION":
                w.events.add(MINION)
                w.played_minion = ent.id
            elif kind == "SPELL":
                w.events.add(SPELL)
                if target is not None and target.tags.get("CARDTYPE") == "MINION":
                    w.events.add(SPELL_ON_MINION)
            if ent.id not in self._hand_at_turn_start:
                w.events.add(FRESH_CARD)
        elif block.type == "ATTACK" and target is not None and target.controller == owner.player_id:
            w.events.add(ATTACK)
            by_minion = kind == "MINION"
            if by_minion:
                w.events.add(MINION_ATTACKS)
            if target.tags.get("CARDTYPE") == "HERO":
                w.events.add(HERO_ATTACKED)
                if by_minion:
                    w.events.add(MINION_ATTACKS_HERO)
            elif target.tags.get("CARDTYPE") == "MINION":
                w.events.add(MINION_ATTACKED)

    def block_end(self, game: Game, block: Block, owner: Player, enemy: Player) -> None:
        w, self._block = self._block, None
        if w is None:
            return
        if w.our_turn:
            if any(game.entities[m].zone == "GRAVEYARD" for m in w.their_minions):
                w.events.add(MINION_DIES)
            if w.cards_played < 3 <= cards_played(game, enemy):
                w.events.add(THIRD_CARD)
            if w.played_minion is not None and game.entities[w.played_minion].zone != "PLAY":
                w.events.discard(MINION)  # gone before "after it's played" secrets could hit it
        self._close(game, w, owner, enemy)

    def step(self, game: Game, step: str, owner: Player, enemy: Player) -> None:
        if step == "MAIN_READY":
            if is_turn(game, enemy):
                self._hand_at_turn_start = {e.id for e in game.entities.values()
                                            if e.controller == enemy.player_id and e.zone == "HAND"}
            elif is_turn(game, owner):
                self._step = self._open(game, owner, enemy, TURN_START)
        elif step == "MAIN_END" and is_turn(game, enemy):
            self._step = self._open(game, owner, enemy, TURN_END)
        elif step in ("MAIN_ACTION", "MAIN_NEXT") and self._step is not None:
            w, self._step = self._step, None
            self._close(game, w, owner, enemy)

    # -- windows ---------------------------------------------------------------

    def _open(self, game: Game, owner: Player, enemy: Player, event: str | None = None) -> Window:
        secrets = secrets_in_play(game, owner)
        return Window(
            secrets=secrets,
            hidden={s for s in secrets if not game.entities[s].card_id},
            facts=facts(game, owner, enemy),
            our_turn=is_turn(game, enemy),
            events={event} if event else set(),
            their_minions=minions(game, owner),
            cards_played=cards_played(game, enemy),
        )

    def _close(self, game: Game, w: Window, owner: Player, enemy: Player) -> None:
        def fired(sid: int) -> bool:
            ent = game.entities[sid]
            return ent.zone != "SECRET" or (sid in w.hidden and bool(ent.card_id))

        went = [s for s in w.secrets if fired(s)]
        stayed = [s for s in w.secrets if not fired(s)]
        for sid in went:
            if card := game.entities[sid].card_id:
                for other in stayed:
                    self.info(other).not_cards.add(base_id(card))
        if went or not w.events:
            return
        held = w.facts & facts(game, owner, enemy)
        turn = (game.turn + 1) // 2
        for sid in stayed:
            if game.entities[sid].tags.get("SECRET_LOCKED") == "1":
                continue
            info = self.info(sid)
            for event in w.events:
                info.survived.append(Survived(event, held, turn))
