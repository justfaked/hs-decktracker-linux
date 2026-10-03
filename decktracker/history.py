"""Match history in SQLite."""

import json
import os
import sqlite3
import threading
from pathlib import Path

# Bump when recorded games gain new fields: older sessions still on disk get
# re-imported and their rows updated.
IMPORT_VERSION = 2

SCHEMA = """
CREATE TABLE IF NOT EXISTS games (
    id INTEGER PRIMARY KEY,
    game_key TEXT UNIQUE NOT NULL,
    session TEXT NOT NULL,
    started_at TEXT NOT NULL,
    ended_at TEXT,
    game_type TEXT,
    format_type TEXT,
    result TEXT,
    went_first INTEGER,
    turns INTEGER,
    my_name TEXT,
    my_hero TEXT,
    my_class TEXT,
    opp_name TEXT,
    opp_hero TEXT,
    opp_class TEXT,
    deck_source TEXT,
    deck_id TEXT,
    deck_name TEXT,
    deck_cards TEXT,
    my_cards TEXT,
    opp_cards TEXT
);
CREATE TABLE IF NOT EXISTS deck_copies (
    deck_id TEXT NOT NULL,
    card_id TEXT NOT NULL,
    copies INTEGER NOT NULL,
    PRIMARY KEY (deck_id, card_id)
);
CREATE TABLE IF NOT EXISTS sessions (
    name TEXT PRIMARY KEY,
    imported INTEGER NOT NULL DEFAULT 0
);
"""

# Columns added after the first release: (name, type).
ADDED_COLUMNS = (("my_opening", "TEXT"), ("my_played", "TEXT"))

JSON_COLUMNS = ("deck_cards", "my_cards", "my_opening", "my_played", "opp_cards")

COLUMNS = (
    "game_key", "session", "started_at", "ended_at", "game_type", "format_type", "result",
    "went_first", "turns", "my_name", "my_hero", "my_class", "opp_name", "opp_hero", "opp_class",
    "deck_source", "deck_id", "deck_name", "deck_cards", "my_cards", "my_opening", "my_played", "opp_cards",
)


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "decktracker"


class History:
    def __init__(self, path: Path | str | None = None):
        if path is None:
            path = data_dir() / "history.db"
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        self._lock = threading.Lock()
        with self._lock, self._db:
            self._db.executescript(SCHEMA)
            existing = {r["name"] for r in self._db.execute("PRAGMA table_info(games)")}
            for name, kind in ADDED_COLUMNS:
                if name not in existing:
                    self._db.execute(f"ALTER TABLE games ADD COLUMN {name} {kind}")

    def add_game(self, record: dict) -> None:
        """Store a finished game, updating it if it was recorded before."""
        row = dict(record)
        for key in JSON_COLUMNS:
            if not isinstance(row.get(key), (str, type(None))):
                row[key] = json.dumps(row[key])
        values = [row.get(c) for c in COLUMNS]
        updates = ", ".join(f"{c} = excluded.{c}" for c in COLUMNS if c != "game_key")
        with self._lock, self._db:
            self._db.execute(
                f"INSERT INTO games ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))}) "
                f"ON CONFLICT(game_key) DO UPDATE SET {updates}",
                values,
            )

    def learn_copies(self, deck_id: str, copies: dict[str, int]) -> None:
        """Remember the most copies of each card seen in one game of this deck."""
        with self._lock, self._db:
            self._db.executemany(
                "INSERT INTO deck_copies (deck_id, card_id, copies) VALUES (?, ?, ?) "
                "ON CONFLICT(deck_id, card_id) DO UPDATE SET copies = MAX(copies, excluded.copies)",
                [(deck_id, card, n) for card, n in copies.items()],
            )

    def copies(self, deck_id: str) -> dict[str, int]:
        with self._lock:
            rows = self._db.execute(
                "SELECT card_id, copies FROM deck_copies WHERE deck_id = ?", (deck_id,)
            ).fetchall()
        return {r["card_id"]: r["copies"] for r in rows}

    def session_imported(self, name: str) -> bool:
        with self._lock:
            row = self._db.execute("SELECT imported FROM sessions WHERE name = ?", (name,)).fetchone()
        return bool(row and row["imported"] >= IMPORT_VERSION)

    def mark_session_imported(self, name: str) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO sessions (name, imported) VALUES (?, ?) "
                "ON CONFLICT(name) DO UPDATE SET imported = excluded.imported",
                (name, IMPORT_VERSION),
            )

    def games(self) -> list[dict]:
        """All recorded games, oldest first, with JSON columns decoded."""
        with self._lock:
            rows = self._db.execute(f"SELECT id, {', '.join(COLUMNS)} FROM games ORDER BY started_at").fetchall()
        out = []
        for r in rows:
            game = dict(r)
            for key in JSON_COLUMNS:
                if game[key]:
                    game[key] = json.loads(game[key])
            out.append(game)
        return out
