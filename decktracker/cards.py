"""Card database backed by HearthstoneJSON, cached on disk."""

import json
import logging
import os
import time
import urllib.request
from pathlib import Path

log = logging.getLogger(__name__)

URL = "https://api.hearthstonejson.com/v1/latest/{locale}/cards.json"
MAX_AGE = 24 * 3600
FIELDS = ("id", "dbfId", "name", "cost", "type", "rarity", "cardClass", "set", "collectible")

CLASS_NAMES = {
    "DEATHKNIGHT": "Death Knight", "DEMONHUNTER": "Demon Hunter", "DRUID": "Druid",
    "HUNTER": "Hunter", "MAGE": "Mage", "PALADIN": "Paladin", "PRIEST": "Priest",
    "ROGUE": "Rogue", "SHAMAN": "Shaman", "WARLOCK": "Warlock", "WARRIOR": "Warrior",
    "NEUTRAL": "Neutral",
}


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache"
    return Path(base) / "decktracker"


class CardDB:
    def __init__(self, cards: list[dict] | None = None, locale: str = "enUS"):
        self.locale = locale
        self._by_id: dict[str, dict] = {}
        self._by_dbf: dict[int, dict] = {}
        for card in cards or []:
            self._add(card)

    def _add(self, card: dict) -> None:
        slim = {k: card[k] for k in FIELDS if k in card}
        self._by_id[slim["id"]] = slim
        if "dbfId" in slim:
            self._by_dbf[slim["dbfId"]] = slim

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, card_id: str) -> dict | None:
        return self._by_id.get(card_id)

    def by_dbf(self, dbf_id: int) -> dict | None:
        return self._by_dbf.get(dbf_id)

    def info(self, card_id: str) -> dict:
        """Card data for the UI; falls back to the bare ID for unknown cards."""
        card = self._by_id.get(card_id)
        if card is None:
            return {"id": card_id, "name": card_id, "cost": None, "type": None, "rarity": None, "cls": None}
        return {
            "id": card_id,
            "name": card.get("name", card_id),
            "cost": card.get("cost"),
            "type": card.get("type"),
            "rarity": card.get("rarity"),
            "cls": card.get("cardClass"),
        }

    def class_of(self, card_id: str) -> str | None:
        card = self._by_id.get(card_id)
        return card.get("cardClass") if card else None

    @classmethod
    def load(cls, locale: str = "enUS", directory: Path | None = None, offline: bool = False) -> "CardDB":
        """Load the cached DB, downloading a fresh copy if it is missing or stale."""
        directory = directory or cache_dir()
        path = directory / f"cards-{locale}.json"
        fresh = path.exists() and time.time() - path.stat().st_mtime < MAX_AGE
        if not fresh and not offline:
            try:
                cls._download(locale, path)
            except Exception as exc:  # network trouble shouldn't stop the tracker
                log.warning("could not download card data (%s); using cached copy if any", exc)
        if not path.exists():
            log.warning("no card data available; cards will show as IDs")
            return cls(locale=locale)
        with path.open(encoding="utf-8") as f:
            return cls(json.load(f), locale)

    @staticmethod
    def _download(locale: str, path: Path) -> None:
        log.info("downloading card data (%s)...", locale)
        req = urllib.request.Request(URL.format(locale=locale), headers={"User-Agent": "decktracker/0.1"})
        with urllib.request.urlopen(req, timeout=60) as resp:
            cards = json.load(resp)
        slim = [{k: c[k] for k in FIELDS if k in c} for c in cards]
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".tmp")
        tmp.write_text(json.dumps(slim, separators=(",", ":")), encoding="utf-8")
        tmp.replace(path)
        log.info("card data cached: %d cards", len(slim))
