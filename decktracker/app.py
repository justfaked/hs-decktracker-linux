"""Background log watcher that keeps a live Tracker for the newest session."""

import logging
import threading
import time
from pathlib import Path

from . import stats
from .cards import CardDB
from .history import History
from .hsmemory import HearthstoneMemory, read_draft_state
from .logfile import Session, session_dirs
from .ratings import MAX_AGE as RATINGS_MAX_AGE, Ratings
from .tracker import Tracker

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.25
MEMORY_POLL_INTERVAL = 0.5
MEMORY_IDLE_INTERVAL = 3.0  # while Hearthstone isn't running or memory can't be read
FEED_BATCH = 5000  # lines fed per lock acquisition during catch-up


class App:
    def __init__(self, install: Path, cards: CardDB, history: History, ratings: Ratings | None = None,
                 read_memory: bool = False, memory_factory=HearthstoneMemory):
        self.install = install
        self._read_memory_at_start = read_memory
        self._memory_factory = memory_factory
        self._memory_stop: threading.Event | None = None  # set while memory reading runs
        self.ratings = ratings or Ratings()
        self.logs_dir = install / "Logs"
        self.cards = cards
        self.history = history
        self.changed = threading.Condition()
        self.version = 0
        self.history_rev = 0
        self.session: Session | None = None
        self.tracker = Tracker(cards, history, ratings=self.ratings)
        self._snapshot: tuple[int, dict] | None = None
        self._stop = threading.Event()

    # -- called from HTTP threads ---------------------------------------------

    def snapshot(self) -> dict:
        with self.changed:
            if self._snapshot is None or self._snapshot[0] != self.version:
                state = self.tracker.snapshot()
                state["history_rev"] = self.history_rev
                state["logs_dir"] = str(self.logs_dir)
                state["watching"] = self.session is not None
                state["locale"] = self.cards.locale
                self._snapshot = (self.version, state)
            return self._snapshot[1]

    def stats(self, query: dict[str, list[str]]) -> dict:
        def arg(name: str, default: str = "all") -> str:
            return (query.get(name) or [default])[0]

        days = arg("days", "")
        return stats.compute(self.history.games(), self.cards, mode=arg("mode"),
                             days=int(days) if days.isdigit() else None, my_class=arg("cls"))

    def set_manual_deck(self, code: str) -> dict:
        with self.changed:
            deck = self.tracker.set_manual_deck(code)
            self._bump()
        return {"name": deck.name, "size": len(deck.cards)}

    # -- watcher thread ---------------------------------------------------------

    def _bump(self) -> None:
        self.version += 1
        self.changed.notify_all()

    def import_old_sessions(self) -> int:
        """Record games from finished sessions that were played while the tracker was off."""
        dirs = session_dirs(self.logs_dir)
        imported = 0
        for folder in dirs[:-1]:
            if self.history.session_imported(folder.name):
                continue
            tracker = Tracker(self.cards, self.history, folder.name, self.ratings)
            for line in Session(folder).read_new():
                tracker.feed(line)
            self.history.mark_session_imported(folder.name)
            imported += len(tracker.recorded)
            log.info("imported %s: %d games", folder.name, len(tracker.recorded))
        if imported:
            with self.changed:
                self.history_rev += 1
                self._bump()
        return imported

    def start(self) -> threading.Thread:
        thread = threading.Thread(target=self._run, name="log-watcher", daemon=True)
        thread.start()
        if self._read_memory_at_start:
            self.set_read_memory(True)
        return thread

    @property
    def read_memory(self) -> bool:
        return self._memory_stop is not None

    def set_read_memory(self, enabled: bool) -> None:
        """Start or stop reading the Arena draft offer from game memory."""
        if enabled == self.read_memory:
            return
        if enabled:
            log.info("memory reading enabled: Arena draft offers are read from the game (read-only)")
            self._memory_stop = threading.Event()
            threading.Thread(target=self._watch_memory, args=(self._memory_factory(), self._memory_stop),
                             name="memory-watcher", daemon=True).start()
        else:
            log.info("memory reading disabled")
            self._memory_stop.set()
            self._memory_stop = None
            with self.changed:
                if self.tracker.set_memory_draft(None, None):
                    self._bump()

    def _watch_memory(self, memory: HearthstoneMemory, stop: threading.Event) -> None:
        last_status = None
        try:
            while not stop.is_set() and not self._stop.is_set():
                state, status = read_draft_state(memory)
                if status != last_status:
                    (log.info if status == "ok" else log.warning)("memory reading: %s", status)
                    last_status = status
                with self.changed:
                    if stop.is_set():  # switched off while reading
                        break
                    if self.tracker.set_memory_draft(state, status):
                        self._bump()
                stop.wait(MEMORY_POLL_INTERVAL if status == "ok" else MEMORY_IDLE_INTERVAL)
        finally:
            memory.close()

    def stop(self) -> None:
        self._stop.set()
        if self._memory_stop is not None:
            self._memory_stop.set()

    def _run(self) -> None:
        try:
            self.import_old_sessions()
        except Exception:
            log.exception("importing old sessions failed")
        last_scan = 0.0
        last_ratings = time.monotonic()
        while not self._stop.is_set():
            now = time.monotonic()
            if now - last_scan > 2 or self.session is None:
                last_scan = now
                self._switch_to_newest()
            if self.session is not None:
                self._feed(self.session.read_new())
            if time.time() - self.ratings.fetched > RATINGS_MAX_AGE and now - last_ratings > 3600:
                last_ratings = now
                self._refresh_ratings()
            self._stop.wait(POLL_INTERVAL)

    def _refresh_ratings(self) -> None:
        ratings = Ratings.load()
        if len(ratings):
            with self.changed:
                self.ratings.data, self.ratings.fetched = ratings.data, ratings.fetched
                self._bump()

    def _switch_to_newest(self) -> None:
        dirs = session_dirs(self.logs_dir)
        if not dirs or (self.session and dirs[-1].name == self.session.name):
            return
        newest = dirs[-1]
        if self.session is not None:
            self._feed(self.session.read_new())
            self.history.mark_session_imported(self.session.name)
        log.info("watching %s", newest)
        tracker = Tracker(self.cards, self.history, newest.name, self.ratings)
        tracker.decks = self.tracker.decks  # remember the last arena/constructed deck
        tracker.memory_status = self.tracker.memory_status
        tracker.memory_draft = self.tracker.memory_draft
        with self.changed:
            self.session = Session(newest)
            self.tracker = tracker
            self._bump()

    def _feed(self, lines: list) -> None:
        for i in range(0, len(lines), FEED_BATCH):
            with self.changed:
                recorded = len(self.tracker.recorded)
                for line in lines[i:i + FEED_BATCH]:
                    self.tracker.feed(line)
                if len(self.tracker.recorded) != recorded:
                    self.history_rev += 1
                self._bump()
