"""A fake process whose memory holds real-layout Mono structures, for testing the memory reader."""

import struct

from decktracker import memory as m
from decktracker.memory import MemoryReadError, Process

BASE = 0x10_0000
SIZE = 0x40_0000


class FakeProcess(Process):
    def __init__(self, heap: "FakeHeap"):
        self.pid = 1234
        self.heap = heap

    def close(self) -> None:
        pass

    def read(self, addr: int, size: int) -> bytes:
        off = addr - BASE
        if off < 0 or off + size > SIZE:
            raise MemoryReadError(f"read outside fake memory at {addr:#x}")
        return bytes(self.heap.mem[off:off + size])

    def module_base(self, filename: str) -> int | None:
        return self.heap.module if filename == "mono-2.0-bdwgc.dll" else None


class FakeHeap:
    """Lays out a Mono runtime: module with exports, root domain, one assembly and its classes."""

    def __init__(self):
        self.mem = bytearray(SIZE)
        self._next = BASE + 0x1000
        self.classes: list[int] = []
        self.layouts: dict[int, tuple] = {}  # class -> (fields, vtable, static data)
        self.list_def = None
        self._build_runtime()

    # -- raw memory ------------------------------------------------------------------

    def alloc(self, size: int) -> int:
        addr = self._next
        self._next += (size + 15) & ~15
        return addr

    def write(self, addr: int, data: bytes) -> None:
        self.mem[addr - BASE:addr - BASE + len(data)] = data

    def put(self, fmt: str, addr: int, *values) -> None:
        self.write(addr, struct.pack("<" + fmt, *values))

    def cstr(self, text: str) -> int:
        addr = self.alloc(len(text) + 1)
        self.write(addr, text.encode() + b"\0")
        return addr

    # -- runtime -----------------------------------------------------------------------

    def _build_runtime(self) -> None:
        self.module = self.alloc(0x1000)
        pe = self.module + 0x80
        self.put("i", self.module + 0x3C, pe - self.module)
        exports = self.alloc(0x40)
        self.put("i", pe + 0x18 + 0x70, exports - self.module)
        name_ptrs, ordinals, functions = self.alloc(8), self.alloc(8), self.alloc(8)
        func = self.alloc(16)
        self.put("i", exports + 0x18, 1)
        self.put("i", exports + 0x1C, functions - self.module)
        self.put("i", exports + 0x20, name_ptrs - self.module)
        self.put("i", exports + 0x24, ordinals - self.module)
        self.put("i", name_ptrs, self.cstr("mono_get_root_domain") - self.module)
        self.put("H", ordinals, 0)
        self.put("i", functions, func - self.module)
        domain_slot = self.alloc(8)
        self.write(func, b"\x48\x8b\x05" + struct.pack("<i", domain_slot - (func + 7)) + b"\xc3")

        domain = self.alloc(512)
        self.put("Q", domain_slot, domain)
        assembly = self.alloc(128)
        self.put("Q", assembly + m.ASSEMBLY_NAME, self.cstr("Assembly-CSharp"))
        node = self.alloc(16)
        self.put("QQ", node, assembly, 0)
        self.put("Q", domain + m.DOMAIN_ASSEMBLIES, node)
        self.image = self.alloc(m.IMAGE_CLASS_CACHE + 64)
        self.put("Q", assembly + m.ASSEMBLY_IMAGE, self.image)

        definition = self.cls("List`1", [("_items", m.T_SZARRAY), ("_size", m.T_I4)], register=False)
        self.list_def = definition

    def finish(self) -> None:
        """Write the class cache once all classes exist (8 buckets, chained)."""
        buckets = 8
        table = self.alloc(8 * buckets)
        heads = [0] * buckets
        for i, klass in enumerate(self.classes):
            bucket = i % buckets
            self.put("Q", klass + m.CLASS_NEXT_CACHE, heads[bucket])
            heads[bucket] = klass
        self.write(table, struct.pack(f"<{buckets}Q", *heads))
        cache = self.image + m.IMAGE_CLASS_CACHE
        self.put("I", cache + m.HASH_TABLE_SIZE, buckets)
        self.put("Q", cache + m.HASH_TABLE_TABLE, table)

    # -- classes and objects -------------------------------------------------------------

    def cls(self, full_name: str, fields: list[tuple], register: bool = True) -> int:
        """fields: (name, mono type) or (name, mono type, "static")."""
        namespace, _, name = full_name.rpartition(".")
        klass = self.alloc(320)
        self.put("B", klass + m.CLASS_KIND, 1)
        self.put("Q", klass + m.CLASS_NAME, self.cstr(name))
        self.put("Q", klass + m.CLASS_NAMESPACE, self.cstr(namespace) if namespace else 0)
        self.put("i", klass + m.CLASS_VTABLE_SIZE, 2)
        array = self.alloc(m.FIELD_SIZE * len(fields))
        instance_offset, static_offset = 16, 0
        layout = {}
        for i, (fname, kind, *flags) in enumerate(fields):
            static = "static" in flags
            mono_type = self.alloc(16)
            self.put("H", mono_type + 8, m.FIELD_ATTR_STATIC if static else 0)
            self.put("B", mono_type + 10, kind)
            if static:
                offset, static_offset = static_offset, static_offset + 8
            else:
                offset, instance_offset = instance_offset, instance_offset + 8
            f = array + i * m.FIELD_SIZE
            self.put("QQQi", f, mono_type, self.cstr(fname), klass, offset)
            layout[fname] = (offset, kind, static)
        self.put("Q", klass + m.CLASS_FIELDS, array)
        self.put("i", klass + m.CLASS_FIELD_COUNT, len(fields))
        vtable = self.alloc(m.VTABLE_SLOTS + 8 * 3)
        self.put("Q", vtable, klass)
        static_data = self.alloc(8 * max(1, static_offset // 8))
        self.put("Q", vtable + m.VTABLE_SLOTS + 8 * 2, static_data)
        runtime_info = self.alloc(16)
        self.put("Q", runtime_info + m.RUNTIME_INFO_VTABLES, vtable)
        self.put("Q", klass + m.CLASS_RUNTIME_INFO, runtime_info)
        self.layouts[klass] = (layout, vtable, static_data)
        if register:
            self.classes.append(klass)
        return klass

    def generic_list(self) -> int:
        """A List<T> instantiation: a GINST class whose fields live on the definition."""
        klass = self.alloc(320)
        self.put("B", klass + m.CLASS_KIND, m.CLASS_KIND_GINST)
        self.put("Q", klass + m.CLASS_NAME, self.cstr("List`1"))
        gclass = self.alloc(16)
        self.put("Q", gclass, self.list_def)
        self.put("Q", klass + m.CLASS_GENERIC_CLASS, gclass)
        vtable = self.alloc(m.VTABLE_SLOTS)
        self.put("Q", vtable, klass)
        self.layouts[klass] = (self.layouts[self.list_def][0], vtable, 0)
        return klass

    def _store(self, addr: int, kind: int, value) -> None:
        if kind == m.T_BOOLEAN:
            self.put("?", addr, value)
        elif kind == m.T_I4:
            self.put("i", addr, value)
        elif kind == m.T_I8:
            self.put("q", addr, value)
        elif kind == m.T_STRING and isinstance(value, str):
            self.put("Q", addr, self.string(value))
        else:
            self.put("Q", addr, value or 0)

    def obj(self, klass: int, **values) -> int:
        layout, vtable, _ = self.layouts[klass]
        addr = self.alloc(16 + 8 * len(layout))
        self.put("Q", addr, vtable)
        for name, value in values.items():
            offset, kind, _ = layout[name]
            self._store(addr + offset, kind, value)
        return addr

    def set_static(self, klass: int, name: str, value) -> None:
        layout, _, static_data = self.layouts[klass]
        offset, kind, _ = layout[name]
        self._store(static_data + offset, kind, value)

    def string(self, text: str) -> int:
        data = text.encode("utf-16-le")
        addr = self.alloc(m.STRING_CHARS + len(data) + 2)
        self.put("i", addr + m.STRING_LENGTH, len(text))
        self.write(addr + m.STRING_CHARS, data)
        return addr

    def array(self, fmt: str, values: list, element_size: int | None = None) -> int:
        size = element_size or struct.calcsize("<" + fmt)
        addr = self.alloc(m.ARRAY_DATA + size * len(values))
        self.put("Q", addr + m.ARRAY_LENGTH, len(values))
        for i, v in enumerate(values):
            self.put(fmt, addr + m.ARRAY_DATA + i * size, *(v if isinstance(v, tuple) else (v,)))
        return addr

    def list_of(self, values: list, fmt: str = "Q", capacity_extra: int = 2) -> int:
        """A List<T> holding pointers (fmt Q) or ints (fmt i), with spare capacity like the real thing."""
        items = self.array(fmt, list(values) + [0] * capacity_extra)
        return self.obj(self.generic_list(), _items=items, _size=len(values))
