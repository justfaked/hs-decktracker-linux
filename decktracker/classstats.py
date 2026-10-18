"""Arena class win rates from Firestone's public stats, cached on disk."""

import gzip
import json
import logging
import time
import urllib.request
from pathlib import Path

from .cards import cache_dir

log = logging.getLogger(__name__)

URL = "https://static.zerotoheroes.com/api/arena/stats/classes/{mode}/last-patch/overview.gz.json"
SOURCE = "Firestone"
MAX_AGE = 24 * 3600
MODES = {"arena": "arena", "underground": "arena-underground"}  # our key -> Firestone's


def parse(data: dict) -> dict[str, list[int]]:
    """{class: [games, wins]} from one mode's overview, summed over hero powers.

    Firestone's winsDistribution counts games by the run's record at the time,
    not runs, so it can't give average wins per run; we only use the win rate.
    """
    out: dict[str, list[int]] = {}
    for entry in data.get("stats", []):
        totals = out.setdefault(entry["playerClass"].upper(), [0, 0])
        totals[0] += entry["totalGames"]
        totals[1] += entry["totalsWins"]
    return out


class ClassStats:
    def __init__(self, data: dict[str, dict[str, list[int]]] | None = None, fetched: float = 0):
        self.data = data or {}  # {"arena" | "underground": {class: [games, wins]}}
        self.fetched = fetched

    def __len__(self) -> int:
        return sum(len(classes) for classes in self.data.values())

    def get(self, underground: bool, cls: str | None) -> dict | None:
        """{"winrate", "games", "rank", "of"} for a class this patch, or None if unknown."""
        classes = self.data.get("underground" if underground else "arena", {})
        if cls not in classes or not classes[cls][0]:
            return None
        rates = {c: wins / games for c, (games, wins) in classes.items() if games}
        rate = rates[cls]
        return {"winrate": round(rate, 4), "games": classes[cls][0],
                "rank": 1 + sum(r > rate for r in rates.values()), "of": len(rates)}

    @classmethod
    def load(cls, directory: Path | None = None, offline: bool = False) -> "ClassStats":
        path = (directory or cache_dir()) / "firestone-arena-classes.json"
        fresh = path.exists() and time.time() - path.stat().st_mtime < MAX_AGE
        if not fresh and not offline:
            try:
                cls._download(path)
            except Exception as exc:  # class stats are optional; keep going without them
                log.warning("could not download arena class stats (%s); using cached copy if any", exc)
        if not path.exists():
            return cls()
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return cls(data, path.stat().st_mtime)

    @staticmethod
    def _download(path: Path) -> None:
        log.info("downloading arena class stats from %s...", SOURCE)
        data = {}
        for ours, theirs in MODES.items():
            req = urllib.request.Request(URL.format(mode=theirs), headers={"User-Agent": "decktracker"})
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read()
            if body[:2] == b"\x1f\x8b":  # served as a gzip file rather than gzip-encoded
                body = gzip.decompress(body)
            data[ours] = parse(json.loads(body))
        if not all(len(classes) >= 5 for classes in data.values()):
            raise ValueError("class stats format changed")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        log.info("arena class stats cached")
