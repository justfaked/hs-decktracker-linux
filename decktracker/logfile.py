"""Incremental reading of Hearthstone's per-session log folders.

Hearthstone (since 2023) writes each run's logs to Logs/Hearthstone_YYYY_MM_DD_HH_MM_SS/.
Lines only carry a time of day, so we derive the date from the folder name and
roll over at midnight.
"""

import heapq
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path

LINE_RE = re.compile(r"^[A-Z] (\d\d):(\d\d):(\d\d)\.(\d+) (.*)$")
SESSION_RE = re.compile(r"^Hearthstone_(\d{4})_(\d\d)_(\d\d)_(\d\d)_(\d\d)_(\d\d)$")

# Order matters only for lines with identical timestamps: deck info before the game.
LOG_KINDS = ("Decks", "Arena", "Hearthstone", "Power")


@dataclass(frozen=True)
class LogLine:
    ts: datetime
    kind: str
    text: str


def session_start(folder: Path) -> datetime | None:
    m = SESSION_RE.match(folder.name)
    return datetime(*map(int, m.groups())) if m else None


def session_dirs(logs_dir: Path) -> list[Path]:
    """Session folders sorted oldest first."""
    if not logs_dir.is_dir():
        return []
    found = [p for p in logs_dir.iterdir() if p.is_dir() and session_start(p)]
    return sorted(found, key=session_start)


class LogFile:
    """Reads complete lines appended to one log file since the last call."""

    def __init__(self, path: Path, kind: str, start: datetime):
        self.path = path
        self.kind = kind
        self.offset = 0
        self._partial = b""
        self._last = start - timedelta(minutes=1)
        self._day = self._last.date()

    def read_new(self) -> list[LogLine]:
        try:
            with self.path.open("rb") as f:
                f.seek(0, 2)
                size = f.tell()
                if size < self.offset:  # truncated/recreated
                    self.offset, self._partial = 0, b""
                f.seek(self.offset)
                data = f.read()
                self.offset = f.tell()
        except FileNotFoundError:
            return []
        if not data:
            return []
        chunks = (self._partial + data).split(b"\n")
        self._partial = chunks.pop()
        out = []
        for raw in chunks:
            line = raw.decode("utf-8", errors="replace").rstrip("\r")
            m = LINE_RE.match(line)
            if not m:
                continue
            h, mi, s, frac, text = m.groups()
            ts = datetime.combine(self._day, datetime.min.time()) + timedelta(
                hours=int(h), minutes=int(mi), seconds=int(s), microseconds=int(frac[:6].ljust(6, "0"))
            )
            if ts < self._last - timedelta(hours=1):  # passed midnight
                self._day += timedelta(days=1)
                ts += timedelta(days=1)
            self._last = ts
            out.append(LogLine(ts, self.kind, text))
        return out


class Session:
    """All relevant logs of one Hearthstone run, merged in time order."""

    def __init__(self, folder: Path):
        self.folder = folder
        self.name = folder.name
        self.start = session_start(folder) or datetime.now()
        self.files = [LogFile(folder / f"{kind}.log", kind, self.start) for kind in LOG_KINDS]

    def read_new(self) -> list[LogLine]:
        per_file = [f.read_new() for f in self.files]
        return list(heapq.merge(*per_file, key=lambda line: line.ts))
