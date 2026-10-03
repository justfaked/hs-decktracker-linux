"""Background log watcher that keeps a live Tracker for the newest session."""

import logging
import threading
import time
from pathlib import Path

from . import stats
from .cards import CardDB
from .history import History
from .logfile import Session, session_dirs
from .ratings import MAX_AGE as RATINGS_MAX_AGE, Ratings
from .tracker import Tracker

log = logging.getLogger(__name__)

POLL_INTERVAL = 0.25
FEED_BATCH = 5000  # lines fed per lock acquisition during catch-up


class App:
    def __init__(self, install: Path, cards: CardDB, history: History, ratings: Ratings | None = None):
        self.install = install
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
        return thread

    def stop(self) -> None:
        self._stop.set()

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
