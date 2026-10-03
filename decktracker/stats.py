"""Aggregate statistics over recorded games."""

from collections import Counter, defaultdict
from datetime import date, datetime, timedelta

from .cards import CardDB
from .decks import ARENA_GAME_TYPES

CLASSES = ("DEATHKNIGHT", "DEMONHUNTER", "DRUID", "HUNTER", "MAGE", "PALADIN",
           "PRIEST", "ROGUE", "SHAMAN", "WARLOCK", "WARRIOR")
LENGTH_BUCKETS = ((1, 5, "≤5"), (6, 8, "6–8"), (9, 11, "9–11"), (12, 14, "12–14"), (15, 999, "15+"))
HOUR_BUCKETS = ((0, 6, "Night (0–6)"), (6, 12, "Morning (6–12)"), (12, 18, "Afternoon (12–18)"),
                (18, 24, "Evening (18–24)"))
WEEKDAYS = ("Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun")
MIN_CARD_GAMES = 2


class Record:
    __slots__ = ("wins", "losses", "ties")

    def __init__(self):
        self.wins = self.losses = self.ties = 0

    def add(self, result: str | None) -> None:
        if result == "WON":
            self.wins += 1
        elif result == "LOST":
            self.losses += 1
        else:
            self.ties += 1

    @property
    def games(self) -> int:
        return self.wins + self.losses + self.ties

    def as_dict(self) -> dict:
        decided = self.wins + self.losses
        return {"games": self.games, "wins": self.wins, "losses": self.losses,
                "winrate": round(self.wins / decided, 4) if decided else None}


def record_of(games: list[dict]) -> dict:
    rec = Record()
    for g in games:
        rec.add(g["result"])
    return rec.as_dict()


def grouped(games: list[dict], key) -> dict:
    groups: dict = defaultdict(Record)
    for g in games:
        k = key(g)
        if k is not None:
            groups[k].add(g["result"])
    return groups


def filter_games(games: list[dict], mode: str = "all", days: int | None = None,
                 my_class: str = "all", now: datetime | None = None) -> list[dict]:
    out = games
    if mode == "arena":
        out = [g for g in out if g["game_type"] in ARENA_GAME_TYPES]
    elif mode and mode != "all":
        out = [g for g in out if g["game_type"] == mode]
    if days:
        cutoff = ((now or datetime.now()) - timedelta(days=days)).isoformat()
        out = [g for g in out if g["started_at"] >= cutoff]
    if my_class and my_class != "all":
        out = [g for g in out if g["my_class"] == my_class]
    return out


def streaks(games: list[dict]) -> dict:
    best_win = best_loss = run = 0
    current = None
    for g in games:  # chronological
        result = g["result"]
        if result not in ("WON", "LOST"):
            continue
        run = run + 1 if result == current else 1
        current = result
        if result == "WON":
            best_win = max(best_win, run)
        else:
            best_loss = max(best_loss, run)
    return {"current": {"result": current, "length": run} if current else None,
            "best_win": best_win, "worst_loss": best_loss}


def duration_minutes(g: dict) -> float | None:
    if not g.get("ended_at"):
        return None
    delta = datetime.fromisoformat(g["ended_at"]) - datetime.fromisoformat(g["started_at"])
    return delta.total_seconds() / 60


def summary(games: list[dict]) -> dict:
    turns = [g["turns"] for g in games if g.get("turns")]
    minutes = [m for m in map(duration_minutes, games) if m is not None]
    return {
        **record_of(games),
        "avg_turns": round(sum(turns) / len(turns), 1) if turns else None,
        "avg_minutes": round(sum(minutes) / len(minutes), 1) if minutes else None,
        "total_hours": round(sum(minutes) / 60, 1) if minutes else 0,
        "streaks": streaks(games),
        "first": record_of([g for g in games if g.get("went_first") == 1]),
        "coin": record_of([g for g in games if g.get("went_first") == 0]),
    }


def matrix(games: list[dict]) -> dict:
    """Win rate of each class we played against each opponent class."""
    cells = grouped(games, lambda g: (g["my_class"], g["opp_class"]) if g["my_class"] and g["opp_class"] else None)
    mine = [c for c in CLASSES if any(k[0] == c for k in cells)]
    theirs = [c for c in CLASSES if any(k[1] == c for k in cells)]
    return {
        "mine": mine,
        "theirs": theirs,
        "cells": {f"{a}|{b}": rec.as_dict() for (a, b), rec in cells.items()},
        "rows": {c: rec.as_dict() for c, rec in grouped(games, lambda g: g["my_class"]).items() if c},
        "cols": {c: rec.as_dict() for c, rec in grouped(games, lambda g: g["opp_class"]).items() if c},
    }


def bucketed(games: list[dict], buckets, value) -> list[dict]:
    out = []
    for lo, hi, label in buckets:
        out.append({"label": label, **record_of([g for g in games if (v := value(g)) is not None and lo <= v <= hi])})
    return out


def daily(games: list[dict]) -> list[dict]:
    if not games:
        return []
    per_day = grouped(games, lambda g: g["started_at"][:10])
    first = date.fromisoformat(min(per_day))
    last = date.fromisoformat(max(per_day))
    out, wins, decided = [], 0, 0
    day = first
    while day <= last:
        rec = per_day.get(day.isoformat(), Record())
        wins += rec.wins
        decided += rec.wins + rec.losses
        out.append({"date": day.isoformat(), **rec.as_dict(),
                    "cumulative_winrate": round(wins / decided, 4) if decided else None})
        day += timedelta(days=1)
    return out


def arena_runs(games: list[dict]) -> dict:
    runs: dict[str, dict] = {}
    for g in games:
        if g["game_type"] not in ARENA_GAME_TYPES or not g.get("deck_id"):
            continue
        run = runs.setdefault(g["deck_id"], {
            "deck_id": g["deck_id"], "name": g["deck_name"], "my_class": g["my_class"],
            "game_type": g["game_type"], "started": g["started_at"], "record": Record(),
        })
        run["record"].add(g["result"])
        run["last"] = g["started_at"]
    rows = []
    for run in runs.values():
        rec = run.pop("record")
        rows.append({**run, **rec.as_dict()})
    rows.sort(key=lambda r: r["started"], reverse=True)

    by_class: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        by_class[r["my_class"]].append(r)
    class_rows = sorted(
        ({"my_class": cls, "runs": len(rs), "avg_wins": round(sum(r["wins"] for r in rs) / len(rs), 2),
          "best": max(r["wins"] for r in rs), **record_of_runs(rs)} for cls, rs in by_class.items() if cls),
        key=lambda r: -r["avg_wins"],
    )
    return {
        "runs": rows,
        "by_class": class_rows,
        "distribution": sorted(Counter(r["wins"] for r in rows).items()),
        "avg_wins": round(sum(r["wins"] for r in rows) / len(rows), 2) if rows else None,
    }


def record_of_runs(runs: list[dict]) -> dict:
    wins = sum(r["wins"] for r in runs)
    losses = sum(r["losses"] for r in runs)
    return {"wins": wins, "losses": losses, "winrate": round(wins / (wins + losses), 4) if wins + losses else None}


def decks(games: list[dict]) -> list[dict]:
    groups: dict[tuple, dict] = {}
    for g in games:
        if not g.get("deck_id"):
            continue
        entry = groups.setdefault((g["deck_source"], g["deck_id"]), {
            "source": g["deck_source"], "deck_id": g["deck_id"], "name": g["deck_name"],
            "my_class": g["my_class"], "first": g["started_at"], "record": Record(),
        })
        entry["record"].add(g["result"])
        entry["last"] = g["started_at"]
    rows = [{**{k: v for k, v in e.items() if k != "record"}, **e["record"].as_dict()} for e in groups.values()]
    return sorted(rows, key=lambda r: r["last"], reverse=True)


def card_stats(games: list[dict], cards: CardDB | None, overall: float | None) -> list[dict]:
    """Per card in our decks: win rate when drawn, when in the opening hand, and when played."""
    drawn, opening, played = defaultdict(Record), defaultdict(Record), defaultdict(Record)
    for g in games:
        for card_id in set(g.get("my_cards") or []):
            drawn[card_id].add(g["result"])
        for card_id in set(g.get("my_opening") or []):
            opening[card_id].add(g["result"])
        for card_id in {e[1] for e in g.get("my_played") or [] if not e[2]}:
            played[card_id].add(g["result"])
    rows = []
    for card_id in set(drawn) | set(played):
        d = drawn[card_id].as_dict()
        if max(d["games"], played[card_id].games) < MIN_CARD_GAMES:
            continue
        rows.append({
            "card": cards.info(card_id) if cards else {"id": card_id, "name": card_id},
            "drawn": d,
            "opening": opening[card_id].as_dict(),
            "played": played[card_id].as_dict(),
            "impact": round(d["winrate"] - overall, 4) if d["winrate"] is not None and overall is not None else None,
        })
    return sorted(rows, key=lambda r: (-r["drawn"]["games"], r["card"]["name"]))


def opponent_card_stats(games: list[dict], cards: CardDB | None) -> list[dict]:
    """Our record against opponents who revealed each card."""
    seen = defaultdict(Record)
    for g in games:
        opp = g.get("opp_cards") or {}
        ids = set(opp.get("deck") or []) | {entry[1] for entry in opp.get("played") or []}
        for card_id in ids:
            seen[card_id].add(g["result"])
    rows = [
        {"card": cards.info(card_id) if cards else {"id": card_id, "name": card_id}, **rec.as_dict()}
        for card_id, rec in seen.items() if rec.games >= MIN_CARD_GAMES
    ]
    return sorted(rows, key=lambda r: (-r["games"], r["card"]["name"]))


def compute(all_games: list[dict], cards: CardDB | None = None, mode: str = "all",
            days: int | None = None, my_class: str = "all", now: datetime | None = None) -> dict:
    games = filter_games(all_games, mode, days, my_class, now)
    overview = summary(games)
    present_modes = Counter(g["game_type"] for g in all_games)
    return {
        "filters": {
            "mode": mode, "days": days, "my_class": my_class,
            "modes": [{"value": m, "games": n} for m, n in present_modes.most_common()],
            "classes": [c for c in CLASSES if any(g["my_class"] == c for g in all_games)],
            "has_arena": any(m in ARENA_GAME_TYPES for m in present_modes),
        },
        "summary": overview,
        "matrix": matrix(games),
        "by_mode": [{"game_type": m, **rec.as_dict()}
                    for m, rec in sorted(grouped(games, lambda g: g["game_type"]).items(), key=lambda kv: -kv[1].games)],
        "turn_order_by_class": [
            {"my_class": c, "first": record_of([g for g in games if g["my_class"] == c and g.get("went_first") == 1]),
             "coin": record_of([g for g in games if g["my_class"] == c and g.get("went_first") == 0])}
            for c in CLASSES if any(g["my_class"] == c for g in games)
        ],
        "by_length": bucketed(games, LENGTH_BUCKETS, lambda g: g.get("turns")),
        "by_hour": [
            {"label": label, **record_of([g for g in games if lo <= int(g["started_at"][11:13]) < hi])}
            for lo, hi, label in HOUR_BUCKETS
        ],
        "by_weekday": [
            {"label": WEEKDAYS[i], **record_of([g for g in games
                                                 if datetime.fromisoformat(g["started_at"]).weekday() == i])}
            for i in range(7)
        ],
        "daily": daily(games),
        "arena": arena_runs(games),
        "decks": decks(games),
        "cards": card_stats(games, cards, overview["winrate"]),
        "opponent_cards": opponent_card_stats(games, cards),
        "recent": [
            {k: g[k] for k in ("id", "started_at", "ended_at", "game_type", "result", "went_first", "turns",
                               "my_class", "opp_class", "opp_name", "deck_name")}
            for g in reversed(games[-200:])
        ],
    }
