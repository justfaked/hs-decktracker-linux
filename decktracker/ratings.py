"""Arena card ratings from HearthArena's tier list, cached on disk."""

import json
import logging
import re
import time
import urllib.request
from pathlib import Path

from .cards import cache_dir

log = logging.getLogger(__name__)

URL = "https://www.heartharena.com/tierlist"
SOURCE = "HearthArena"
MAX_AGE = 24 * 3600

RE_SECTION = re.compile(r'<section class="tab tierlist ([a-z-]+)')
RE_ITEM = re.compile(
    r'<li class="tier [a-z-]+"><header[^>]*>([^<]+)</header>'  # tier heading
    r'|renders/\w+/([A-Za-z0-9_]+)\.webp">[^<]*</dt><dd class="score[^"]*">(-?\d+)</dd>'  # card + score
)


def section_class(name: str) -> str:
    """'death-knight' -> 'DEATHKNIGHT'; the class-agnostic 'any' list -> 'NEUTRAL'."""
    return "NEUTRAL" if name == "any" else name.replace("-", "").upper()


def parse_tierlist(html: str) -> dict[str, dict[str, list]]:
    """{class: {card_id: [score, tier]}} from the tier list page."""
    out: dict[str, dict[str, list]] = {}
    sections = list(RE_SECTION.finditer(html))
    for i, section in enumerate(sections):
        end = sections[i + 1].start() if i + 1 < len(sections) else len(html)
        cards = out.setdefault(section_class(section[1]), {})
        tier = ""
        for m in RE_ITEM.finditer(html, section.end(), end):
            if m[1]:
                tier = m[1].strip()
            else:
                cards[m[2]] = [int(m[3]), tier]
    return out


class Ratings:
    def __init__(self, data: dict[str, dict[str, list]] | None = None, fetched: float = 0):
        self.data = data or {}
        self.fetched = fetched

    def __len__(self) -> int:
        return sum(len(cards) for cards in self.data.values())

    def get(self, cls: str | None, card_id: str) -> tuple[int, str] | None:
        """(score, tier) of a card when drafted as `cls`, or None if unrated."""
        for key in (cls, "NEUTRAL"):
            entry = self.data.get(key or "", {}).get(card_id)
            if entry:
                return entry[0], entry[1]
        return None

    def for_class(self, cls: str) -> dict[str, list]:
        return self.data.get(cls, {})

    @classmethod
    def load(cls, directory: Path | None = None, offline: bool = False) -> "Ratings":
        path = (directory or cache_dir()) / "heartharena.json"
        fresh = path.exists() and time.time() - path.stat().st_mtime < MAX_AGE
        if not fresh and not offline:
            try:
                cls._download(path)
            except Exception as exc:  # ratings are optional; keep going without them
                log.warning("could not download arena ratings (%s); using cached copy if any", exc)
        if not path.exists():
            return cls()
        with path.open(encoding="utf-8") as f:
            data = json.load(f)
        return cls(data, path.stat().st_mtime)

    @staticmethod
    def _download(path: Path) -> None:
        log.info("downloading arena ratings from %s...", SOURCE)
        req = urllib.request.Request(URL, headers={"User-Agent": "Mozilla/5.0 (decktracker; personal use)"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            html = resp.read().decode("utf-8", errors="replace")
        data = parse_tierlist(html)
        if sum(len(c) for c in data.values()) < 100:
            raise ValueError("tier list page format changed")
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        log.info("arena ratings cached: %d entries", sum(len(c) for c in data.values()))
