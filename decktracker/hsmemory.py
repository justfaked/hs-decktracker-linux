"""Hearthstone-specific memory paths (Arena draft), on top of the generic Mono reader.

Field names follow Firestone's open-source reader (Zero-to-Heroes/unity-spy-.net4.5).
"""

from dataclasses import dataclass, field

from .memory import Mono, MemoryReadError, Obj, Process, find_hearthstone_pid

DRAFT_MODES = ("INVALID", "NO_ACTIVE_DRAFT", "DRAFTING", "ACTIVE_DRAFT_DECK", "IN_REWARDS", "REDRAFTING")
SLOT_TYPES = ("NONE", "CARD", "HERO", "HERO_POWER")
DICT_ENTRY_SIZE = 24  # Dictionary<K,V>.Entry for reference K/V: hashCode, next, key, value
DICT_ENTRY_VALUE = 16


@dataclass
class DraftChoice:
    card_id: str
    package: list[str] = field(default_factory=list)  # extra cards bundled with a legendary pick


@dataclass
class DraftState:
    mode: str
    slot: str
    underground: bool
    choices: list[DraftChoice]
    deck_id: str = ""
    hero: str = ""
    deck: list[str] = field(default_factory=list)  # exact card list, one entry per copy
    wins: int = 0
    losses: int = 0


class HearthstoneMemory:
    """Connects to the running Hearthstone process; reconnects when it restarts."""

    def __init__(self, process_factory=Process, pid_finder=find_hearthstone_pid):
        self._process_factory = process_factory
        self._pid_finder = pid_finder
        self.pid: int | None = None
        self.mono: Mono | None = None
        self._draft_manager: int | None = None

    def connect(self) -> bool:
        pid = self._pid_finder()
        if pid is None:
            self.close()
            return False
        if pid != self.pid or self.mono is None:
            self.close()
            self.mono = Mono(self._process_factory(pid))
            self.pid = pid
        return True

    def close(self) -> None:
        if self.mono is not None:
            close = getattr(self.mono.p, "close", None)
            if close:
                close()
        self.mono = None
        self.pid = None
        self._draft_manager = None

    def service(self, name: str) -> Obj | None:
        """A service from Hearthstone's service locator (e.g. DraftManager)."""
        mono = self.mono
        builders = mono.static("Hearthstone.HearthstoneJobs", "s_dependencyBuilder")
        first = builders.list_objects()[0] if builders else None
        services = first["m_serviceLocator"]["m_services"] if first else None
        entries = services["_entries"] if services else None
        if entries is None:
            return None
        for i in range(entries.length):
            value = mono.p.ptr(entries.element_addr(i, DICT_ENTRY_SIZE) + DICT_ENTRY_VALUE)
            if not value:
                continue
            info = Obj(mono, value)
            if info.get("<ServiceTypeName>k__BackingField") == name:
                return info["<Service>k__BackingField"]
        return None

    def _draft_manager_obj(self) -> Obj | None:
        if self._draft_manager:
            obj = Obj(self.mono, self._draft_manager)
            if obj.class_name == "DraftManager":
                return obj
        obj = self.service("DraftManager")
        self._draft_manager = obj.addr if obj else None
        return obj

    def draft_state(self) -> DraftState | None:
        """The Arena draft screen's state, or None when it isn't open."""
        display = self.mono.static("DraftDisplay", "s_instance")
        if display is None:
            return None
        mode = display["m_currentMode"]
        choices = []
        options = display["m_choices"]
        for choice in options.list_objects() if options else []:
            package = choice.get("m_packageCardIds")
            choices.append(DraftChoice(card_id=choice["m_cardID"] or "",
                                       package=package.list_strings() if package else []))

        state = DraftState(mode=DRAFT_MODES[mode] if 0 <= mode < len(DRAFT_MODES) else str(mode),
                           slot="NONE", underground=False, choices=choices)
        manager = self._draft_manager_obj()
        if manager is None:
            return state
        state.underground = bool(manager["m_undergroundActive"])
        prefix = "m_currentUnderground" if state.underground else "m_current"
        slot = manager[f"{prefix}SlotType"]
        state.slot = SLOT_TYPES[slot] if 0 <= slot < len(SLOT_TYPES) else str(slot)
        state.wins = manager["m_undergroundWins" if state.underground else "m_wins"] or 0
        state.losses = manager["m_undergroundLosses" if state.underground else "m_losses"] or 0
        deck = manager["m_undergroundDraftDeck" if state.underground else "m_draftDeck"]
        if deck is not None:
            state.deck_id = str(deck.get("ID") or "")
            state.hero = deck.get("<HeroCardID>k__BackingField") or ""
            slots = deck["m_slots"]
            for slot_obj in slots.list_objects() if slots else []:
                counts = slot_obj["m_count"]  # normal / golden / diamond / ... copies
                state.deck += [slot_obj["m_cardId"]] * sum(counts.list_ints() if counts else [])
        return state


def read_draft_state(memory: HearthstoneMemory) -> tuple[DraftState | None, str]:
    """One poll: (state, status message). Never raises."""
    try:
        if not memory.connect():
            return None, "Hearthstone is not running"
        return memory.draft_state(), "ok"
    except (MemoryReadError, OSError) as exc:
        memory.close()
        return None, f"memory not readable: {exc}"
