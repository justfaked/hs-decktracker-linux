import unittest
from datetime import datetime

from decktracker import memory as m
from decktracker.hsmemory import DraftChoice, DraftState, HearthstoneMemory, read_draft_state
from decktracker.memory import Mono, MemoryReadError
from tests.fake_mono import FakeHeap, FakeProcess
from tests.helpers import CARDS
from tests.test_ratings import RATINGS
from tests.test_tracker import run_session
from tests.test_ratings import draft_log


def hearthstone_heap(display: bool = True, choices=("CARD_A", "CARD_B", "CARD_C")) -> FakeHeap:
    """Hearthstone's draft-related objects, laid out like the real game."""
    h = FakeHeap()
    choice_cls = h.cls("DraftChoice", [("m_cardID", m.T_STRING), ("m_packageCardIds", m.T_GENERICINST)])
    display_cls = h.cls("DraftDisplay", [("s_instance", m.T_CLASS, "static"), ("m_currentMode", m.T_VALUETYPE),
                                         ("m_choices", m.T_GENERICINST)])
    slot_cls = h.cls("DeckSlot", [("m_cardId", m.T_STRING), ("m_count", m.T_GENERICINST)])
    deck_cls = h.cls("CollectionDeck", [("ID", m.T_I8), ("<HeroCardID>k__BackingField", m.T_STRING),
                                        ("m_slots", m.T_GENERICINST)])
    manager_cls = h.cls("DraftManager", [
        ("m_undergroundActive", m.T_BOOLEAN), ("m_currentUndergroundSlotType", m.T_VALUETYPE),
        ("m_currentSlotType", m.T_VALUETYPE), ("m_undergroundWins", m.T_I4), ("m_undergroundLosses", m.T_I4),
        ("m_wins", m.T_I4), ("m_losses", m.T_I4),
        ("m_undergroundDraftDeck", m.T_CLASS), ("m_draftDeck", m.T_CLASS)])
    info_cls = h.cls("ServiceInfo", [("<ServiceTypeName>k__BackingField", m.T_STRING),
                                     ("<Service>k__BackingField", m.T_OBJECT)])
    dict_cls = h.cls("Dictionary`2", [("_entries", m.T_SZARRAY)])
    locator_cls = h.cls("ServiceLocator", [("m_services", m.T_CLASS)])
    dependency_cls = h.cls("ServiceDependency", [("m_serviceLocator", m.T_CLASS)])
    jobs_cls = h.cls("Hearthstone.HearthstoneJobs", [("s_dependencyBuilder", m.T_GENERICINST, "static")])

    slots = [h.obj(slot_cls, m_cardId="CARD_A", m_count=h.list_of([2, 0], fmt="i")),
             h.obj(slot_cls, m_cardId="CARD_B", m_count=h.list_of([0, 1], fmt="i"))]  # one golden copy
    deck = h.obj(deck_cls, ID=777, **{"<HeroCardID>k__BackingField": "HERO_09"}, m_slots=h.list_of(slots))
    manager = h.obj(manager_cls, m_undergroundActive=True, m_currentUndergroundSlotType=1,
                    m_undergroundWins=3, m_undergroundLosses=1, m_undergroundDraftDeck=deck)
    other = h.obj(info_cls, **{"<ServiceTypeName>k__BackingField": "CameraManager"})
    info = h.obj(info_cls, **{"<ServiceTypeName>k__BackingField": "DraftManager",
                              "<Service>k__BackingField": manager})
    entries = h.array("iiQQ", [(1, -1, 0, other), (0, 0, 0, 0), (2, -1, 0, info)], element_size=24)
    services = h.obj(dict_cls, _entries=entries)
    dependency = h.obj(dependency_cls, m_serviceLocator=h.obj(locator_cls, m_services=services))
    h.set_static(jobs_cls, "s_dependencyBuilder", h.list_of([dependency]))

    if display:
        options = [h.obj(choice_cls, m_cardID=c, m_packageCardIds=h.list_of(
            [h.string("CARD_Z")] if c == "CARD_C" else [])) for c in choices]
        instance = h.obj(display_cls, m_currentMode=2, m_choices=h.list_of(options))
        h.set_static(display_cls, "s_instance", instance)
    h.finish()
    return h


def connect(heap: FakeHeap) -> HearthstoneMemory:
    return HearthstoneMemory(process_factory=lambda pid: FakeProcess(heap), pid_finder=lambda: 1234)


class MonoReaderTest(unittest.TestCase):
    def test_root_domain_classes_and_fields(self):
        heap = hearthstone_heap()
        mono = Mono(FakeProcess(heap))
        self.assertIn("Hearthstone.HearthstoneJobs", mono.classes())
        self.assertIn("DraftDisplay", mono.classes())
        display = mono.static("DraftDisplay", "s_instance")
        self.assertEqual(display.class_name, "DraftDisplay")
        self.assertEqual(display["m_currentMode"], 2)
        # List<T> is a generic instance: its fields come from the generic definition.
        self.assertEqual([c["m_cardID"] for c in display["m_choices"].list_objects()], ["CARD_A", "CARD_B", "CARD_C"])

    def test_unknown_field_raises(self):
        mono = Mono(FakeProcess(hearthstone_heap()))
        with self.assertRaises(MemoryReadError):
            mono.static("DraftDisplay", "s_instance")["m_nope"]

    def test_missing_mono_module(self):
        heap = hearthstone_heap()
        heap.module = None
        with self.assertRaises(MemoryReadError):
            Mono(FakeProcess(heap))


class DraftStateTest(unittest.TestCase):
    def test_reads_offer_and_exact_deck(self):
        state, status = read_draft_state(connect(hearthstone_heap()))
        self.assertEqual(status, "ok")
        self.assertEqual((state.mode, state.slot, state.underground), ("DRAFTING", "CARD", True))
        self.assertEqual([c.card_id for c in state.choices], ["CARD_A", "CARD_B", "CARD_C"])
        self.assertEqual(state.choices[2].package, ["CARD_Z"])
        self.assertEqual((state.deck_id, state.hero, state.wins, state.losses), ("777", "HERO_09", 3, 1))
        self.assertEqual(state.deck, ["CARD_A", "CARD_A", "CARD_B"])  # normal + golden copies counted

    def test_no_draft_screen(self):
        state, status = read_draft_state(connect(hearthstone_heap(display=False)))
        self.assertEqual((state, status), (None, "ok"))

    def test_not_running_and_unreadable(self):
        memory = HearthstoneMemory(process_factory=lambda pid: FakeProcess(FakeHeap()), pid_finder=lambda: None)
        self.assertEqual(read_draft_state(memory), (None, "Hearthstone is not running"))

        def denied(pid):
            raise MemoryReadError("permission denied")
        memory = HearthstoneMemory(process_factory=denied, pid_finder=lambda: 1234)
        state, status = read_draft_state(memory)
        self.assertIsNone(state)
        self.assertIn("permission denied", status)


class TrackerMemoryTest(unittest.TestCase):
    STATE = DraftState(mode="DRAFTING", slot="CARD", underground=True, deck_id="50", hero="HERO_09",
                       choices=[DraftChoice("CARD_A"), DraftChoice("CARD_B"), DraftChoice("CARD_C", ["CARD_Z"])],
                       deck=["CARD_A", "CARD_A", "CARD_C"])

    def tracker(self):
        tracker = run_session({"Arena": draft_log()})
        tracker.ratings = RATINGS
        return tracker

    def test_offer_with_ratings_and_best_pick(self):
        tracker = self.tracker()
        self.assertTrue(tracker.set_memory_draft(self.STATE, "ok"))
        self.assertFalse(tracker.set_memory_draft(self.STATE, "ok"))  # unchanged
        state = tracker.snapshot()
        self.assertEqual(state["status"], "drafting")
        offer = state["draft"]["offer"]
        self.assertEqual([(c["card"]["id"], c["rating"]["score"], c["best"]) for c in offer["choices"]],
                         [("CARD_A", 110, True), ("CARD_B", 40, False), ("CARD_C", 75, False)])
        self.assertEqual(offer["choices"][2]["package"][0]["card"]["id"], "CARD_Z")

    def test_exact_deck_replaces_arena_log_guess(self):
        tracker = self.tracker()
        tracker.set_memory_draft(self.STATE, "ok")
        draft = tracker.snapshot()["draft"]
        self.assertEqual(draft["count"], 3)
        # Logged picks were A, B; B isn't really in the deck, C came from elsewhere.
        self.assertEqual([p["card"]["id"] for p in draft["picks"]], ["CARD_A", "CARD_C", "CARD_A"])
        self.assertTrue(tracker.decks.arena.exact)

    def test_memory_decides_whether_drafting(self):
        tracker = self.tracker()
        tracker.set_memory_draft(None, "ok")  # draft screen closed
        self.assertEqual(tracker.snapshot()["status"], "idle")
        tracker.set_memory_draft(None, "Hearthstone is not running")  # fall back to the logs
        self.assertEqual(tracker.snapshot()["status"], "drafting")

    def test_hero_choice_is_not_rated(self):
        tracker = self.tracker()
        tracker.set_memory_draft(DraftState(mode="DRAFTING", slot="HERO", underground=False,
                                            choices=[DraftChoice("HERO_09"), DraftChoice("HERO_08")]), "ok")
        offer = tracker.snapshot()["draft"]["offer"]
        self.assertEqual(offer["slot"], "HERO")
        self.assertTrue(all(c["rating"] is None and not c["best"] for c in offer["choices"]))

    def test_memory_status_in_snapshot(self):
        tracker = self.tracker()
        self.assertEqual(tracker.snapshot()["memory"], {"enabled": False, "status": None})
        tracker.set_memory_draft(None, "ok")
        self.assertEqual(tracker.snapshot()["memory"], {"enabled": True, "status": "ok"})


if __name__ == "__main__":
    unittest.main()
