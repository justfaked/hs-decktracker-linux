"""Encode/decode Hearthstone deck codes (the "AAE..." strings shared between players)."""

import base64
from dataclasses import dataclass, field

FORMATS = {1: "FT_WILD", 2: "FT_STANDARD", 3: "FT_CLASSIC", 4: "FT_TWIST"}


@dataclass
class DeckDefinition:
    heroes: list[int]
    cards: dict[int, int]  # dbfId -> count
    format: int = 2
    sideboards: list[tuple[int, int, int]] = field(default_factory=list)  # (dbfId, count, owner dbfId)


def _read_varint(data: bytes, pos: int) -> tuple[int, int]:
    result = shift = 0
    while True:
        if pos >= len(data):
            raise ValueError("unexpected end of deck code")
        byte = data[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7


def _write_varint(value: int) -> bytes:
    out = bytearray()
    while True:
        byte = value & 0x7F
        value >>= 7
        if value:
            out.append(byte | 0x80)
        else:
            out.append(byte)
            return bytes(out)


def decode(deckstring: str) -> DeckDefinition:
    deckstring = deckstring.strip()
    data = base64.b64decode(deckstring + "=" * (-len(deckstring) % 4))
    if not data or data[0] != 0:
        raise ValueError("not a deck code")
    version, pos = _read_varint(data, 1)
    if version != 1:
        raise ValueError(f"unsupported deck code version {version}")
    fmt, pos = _read_varint(data, pos)

    n, pos = _read_varint(data, pos)
    heroes = []
    for _ in range(n):
        hero, pos = _read_varint(data, pos)
        heroes.append(hero)

    cards: dict[int, int] = {}
    for copies in (1, 2):
        n, pos = _read_varint(data, pos)
        for _ in range(n):
            dbf, pos = _read_varint(data, pos)
            cards[dbf] = copies
    n, pos = _read_varint(data, pos)
    for _ in range(n):
        dbf, pos = _read_varint(data, pos)
        cards[dbf], pos = _read_varint(data, pos)

    sideboards = []
    if pos < len(data) and data[pos] == 1:
        pos += 1
        for copies in (1, 2):
            n, pos = _read_varint(data, pos)
            for _ in range(n):
                dbf, pos = _read_varint(data, pos)
                owner, pos = _read_varint(data, pos)
                sideboards.append((dbf, copies, owner))
        n, pos = _read_varint(data, pos)
        for _ in range(n):
            dbf, pos = _read_varint(data, pos)
            count, pos = _read_varint(data, pos)
            owner, pos = _read_varint(data, pos)
            sideboards.append((dbf, count, owner))

    return DeckDefinition(heroes=heroes, cards=cards, format=fmt, sideboards=sideboards)


def encode(deck: DeckDefinition) -> str:
    out = bytearray(b"\x00")
    out += _write_varint(1)
    out += _write_varint(deck.format)
    out += _write_varint(len(deck.heroes))
    for hero in sorted(deck.heroes):
        out += _write_varint(hero)

    for copies in (1, 2):
        group = sorted(dbf for dbf, c in deck.cards.items() if c == copies)
        out += _write_varint(len(group))
        for dbf in group:
            out += _write_varint(dbf)
    many = sorted((dbf, c) for dbf, c in deck.cards.items() if c > 2)
    out += _write_varint(len(many))
    for dbf, count in many:
        out += _write_varint(dbf) + _write_varint(count)

    if deck.sideboards:
        out.append(1)
        for copies in (1, 2):
            group = sorted((d, o) for d, c, o in deck.sideboards if c == copies)
            out += _write_varint(len(group))
            for dbf, owner in group:
                out += _write_varint(dbf) + _write_varint(owner)
        many_sb = sorted((d, c, o) for d, c, o in deck.sideboards if c > 2)
        out += _write_varint(len(many_sb))
        for dbf, count, owner in many_sb:
            out += _write_varint(dbf) + _write_varint(count) + _write_varint(owner)

    return base64.b64encode(bytes(out)).decode()
