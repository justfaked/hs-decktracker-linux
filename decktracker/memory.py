"""Read-only access to Hearthstone's managed (Mono) memory.

Hearthstone is a Unity game on the Mono runtime. Running under Wine/Proton it is
an ordinary Linux process, so its memory can be read through /proc/<pid>/mem
(same user, kernel.yama.ptrace_scope 0). Nothing is ever written.

Struct offsets are for Unity 6000.3 x64 (mono-2.0-bdwgc.dll), taken from the
MIT-licensed UnitySpy fork used by Firestone (Zero-to-Heroes/unity-spy-.net4.5),
where they were validated against Hearthstone.
"""

import os
import struct
from dataclasses import dataclass
from pathlib import Path

# MonoDomain / MonoAssembly / MonoImage
DOMAIN_ASSEMBLIES = 160
ASSEMBLY_NAME = 16
ASSEMBLY_IMAGE = 96
IMAGE_CLASS_CACHE = 1232
HASH_TABLE_SIZE = 24
HASH_TABLE_TABLE = 32
# MonoClass
CLASS_KIND = 27
CLASS_PARENT = 48
CLASS_NAME = 72
CLASS_NAMESPACE = 80
CLASS_VTABLE_SIZE = 92
CLASS_FIELDS = 152
CLASS_RUNTIME_INFO = 208
CLASS_GENERIC_CLASS = 240
CLASS_FIELD_COUNT = 256
CLASS_NEXT_CACHE = 264
FIELD_SIZE = 32
RUNTIME_INFO_VTABLES = 8
VTABLE_SLOTS = 72
# Managed objects
STRING_LENGTH = 16
STRING_CHARS = 20
ARRAY_LENGTH = 24
ARRAY_DATA = 32

CLASS_KIND_GINST = 3
FIELD_ATTR_STATIC = 0x10
FIELD_ATTR_LITERAL = 0x40

# MonoTypeEnum
T_BOOLEAN, T_CHAR, T_I1, T_U1, T_I2, T_U2, T_I4, T_U4, T_I8, T_U8, T_R4, T_R8, T_STRING = range(0x02, 0x0f)
T_VALUETYPE, T_CLASS, T_GENERICINST, T_OBJECT, T_SZARRAY = 0x11, 0x12, 0x15, 0x1c, 0x1d
REFERENCE_TYPES = {T_STRING, T_CLASS, T_GENERICINST, T_OBJECT, T_SZARRAY, 0x14}
PRIMITIVES = {
    T_BOOLEAN: "?", T_CHAR: "H", T_I1: "b", T_U1: "B", T_I2: "h", T_U2: "H", T_I4: "i",
    T_U4: "I", T_I8: "q", T_U8: "Q", T_R4: "f", T_R8: "d",
}


class MemoryReadError(Exception):
    """Memory could not be read (process gone, permission denied, layout changed)."""


def find_hearthstone_pid() -> int | None:
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        try:
            cmdline = (entry / "cmdline").read_bytes()
        except OSError:
            continue
        exe = cmdline.split(b"\0", 1)[0]
        if exe.lower().endswith(b"hearthstone.exe"):
            return int(entry.name)
    return None


class Process:
    def __init__(self, pid: int):
        self.pid = pid
        try:
            self._fd = os.open(f"/proc/{pid}/mem", os.O_RDONLY)
        except OSError as exc:
            raise MemoryReadError(f"cannot open memory of process {pid}: {exc}") from exc

    def close(self) -> None:
        os.close(self._fd)

    def read(self, addr: int, size: int) -> bytes:
        try:
            data = os.pread(self._fd, size, addr)
        except OSError as exc:
            raise MemoryReadError(f"read of {size} bytes at {addr:#x} failed: {exc}") from exc
        if len(data) != size:
            raise MemoryReadError(f"short read at {addr:#x}")
        return data

    def unpack(self, fmt: str, addr: int):
        return struct.unpack_from("<" + fmt, self.read(addr, struct.calcsize("<" + fmt)))[0]

    def ptr(self, addr: int) -> int:
        return self.unpack("Q", addr)

    def i32(self, addr: int) -> int:
        return self.unpack("i", addr)

    def cstring(self, addr: int, limit: int = 256) -> str:
        if not addr:
            return ""
        data = self.read(addr, limit)
        return data.split(b"\0", 1)[0].decode("utf-8", errors="replace")

    def module_base(self, filename: str) -> int | None:
        filename = filename.lower()
        with open(f"/proc/{self.pid}/maps") as f:
            for line in f:
                parts = line.split(maxsplit=5)  # the path may contain spaces
                if len(parts) == 6 and parts[5].strip().lower().endswith("/" + filename) and int(parts[2], 16) == 0:
                    return int(parts[0].split("-")[0], 16)
        return None


@dataclass(frozen=True)
class Field:
    name: str
    type: int
    offset: int
    static: bool
    type_data: int  # MonoType.data (class / generic class pointer)


class Mono:
    """The Mono runtime inside a Hearthstone process."""

    def __init__(self, process: Process, assembly: str = "Assembly-CSharp"):
        self.p = process
        base = process.module_base("mono-2.0-bdwgc.dll")
        if base is None:
            raise MemoryReadError("mono-2.0-bdwgc.dll is not loaded")
        func = self._export(base, b"mono_get_root_domain")
        # x64: mov rax, [rip+disp32]; ret
        if self.p.read(func, 3) != b"\x48\x8b\x05":
            raise MemoryReadError("unexpected mono_get_root_domain code")
        self.domain = self.p.ptr(func + 7 + self.p.i32(func + 3))
        self.image = self._assembly_image(assembly)
        self._classes: dict[str, int] | None = None
        self._fields: dict[int, dict[str, Field]] = {}

    def _export(self, base: int, name: bytes) -> int:
        pe = base + self.p.i32(base + 0x3C)
        export_rva = self.p.i32(pe + 0x18 + 0x70)  # PE32+ optional header, data directory 0
        exports = base + export_rva
        count = self.p.i32(exports + 0x18)
        functions = base + self.p.i32(exports + 0x1C)
        names = base + self.p.i32(exports + 0x20)
        ordinals = base + self.p.i32(exports + 0x24)
        for i in range(count):
            if self.p.cstring(base + self.p.i32(names + 4 * i), 64).encode() == name:
                ordinal = self.p.unpack("H", ordinals + 2 * i)
                return base + self.p.i32(functions + 4 * ordinal)
        raise MemoryReadError(f"export {name.decode()} not found")

    def _assembly_image(self, name: str) -> int:
        node = self.p.ptr(self.domain + DOMAIN_ASSEMBLIES)
        for _ in range(1000):
            if not node:
                break
            assembly = self.p.ptr(node)
            if assembly and self.p.cstring(self.p.ptr(assembly + ASSEMBLY_NAME), 64) == name:
                return self.p.ptr(assembly + ASSEMBLY_IMAGE)
            node = self.p.ptr(node + 8)
        raise MemoryReadError(f"assembly {name} not loaded")

    # -- classes ------------------------------------------------------------------

    def classes(self) -> dict[str, int]:
        """All classes of the assembly by full name (namespace.Name)."""
        if self._classes is None:
            cache = self.image + IMAGE_CLASS_CACHE
            size = self.p.unpack("I", cache + HASH_TABLE_SIZE)
            table = self.p.ptr(cache + HASH_TABLE_TABLE)
            buckets = struct.unpack(f"<{size}Q", self.p.read(table, 8 * size))
            classes = {}
            for klass in buckets:
                while klass:
                    name = self.p.cstring(self.p.ptr(klass + CLASS_NAME), 128)
                    namespace = self.p.cstring(self.p.ptr(klass + CLASS_NAMESPACE), 128)
                    classes[f"{namespace}.{name}" if namespace else name] = klass
                    klass = self.p.ptr(klass + CLASS_NEXT_CACHE)
            self._classes = classes
        return self._classes

    def find_class(self, full_name: str) -> int:
        klass = self.classes().get(full_name)
        if klass is None:
            raise MemoryReadError(f"class {full_name} not found")
        return klass

    def fields(self, klass: int) -> dict[str, Field]:
        """Fields of a class including inherited ones."""
        if klass in self._fields:
            return self._fields[klass]
        out: dict[str, Field] = {}
        current = klass
        for _ in range(32):
            if not current:
                break
            definition = current
            if self.p.unpack("B", current + CLASS_KIND) & 0x7 == CLASS_KIND_GINST:
                # Generic instances share the field layout of their generic definition.
                definition = self.p.ptr(self.p.ptr(current + CLASS_GENERIC_CLASS))
            fields_ptr = self.p.ptr(definition + CLASS_FIELDS)
            count = self.p.i32(definition + CLASS_FIELD_COUNT)
            for i in range(count if fields_ptr else 0):
                f = fields_ptr + i * FIELD_SIZE
                type_ptr = self.p.ptr(f)
                name = self.p.cstring(self.p.ptr(f + 8), 128)
                attrs = self.p.unpack("H", type_ptr + 8)
                if attrs & FIELD_ATTR_LITERAL or name in out:
                    continue
                out[name] = Field(name=name, type=self.p.unpack("B", type_ptr + 10),
                                  offset=self.p.i32(f + 24), static=bool(attrs & FIELD_ATTR_STATIC),
                                  type_data=self.p.ptr(type_ptr))
            current = self.p.ptr(current + CLASS_PARENT)
        self._fields[klass] = out
        return out

    def static(self, class_name: str, field_name: str):
        klass = self.find_class(class_name)
        field = self.fields(klass)[field_name]
        runtime_info = self.p.ptr(klass + CLASS_RUNTIME_INFO)
        if not runtime_info:
            return None  # class not initialised yet
        vtable = self.p.ptr(runtime_info + RUNTIME_INFO_VTABLES)
        if not vtable:
            return None
        data = self.p.ptr(vtable + VTABLE_SLOTS + 8 * self.p.i32(klass + CLASS_VTABLE_SIZE))
        return self._value(data + field.offset, field.type) if data else None

    # -- objects ------------------------------------------------------------------

    def _value(self, addr: int, kind: int):
        if kind in PRIMITIVES:
            return self.p.unpack(PRIMITIVES[kind], addr)
        if kind == T_VALUETYPE:  # enums and small structs: read as int
            return self.p.i32(addr)
        if kind in REFERENCE_TYPES:
            ref = self.p.ptr(addr)
            if not ref:
                return None
            return self.string(ref) if kind == T_STRING else Obj(self, ref)
        return None

    def string(self, addr: int) -> str:
        length = self.p.i32(addr + STRING_LENGTH)
        if not 0 <= length < 100_000:
            raise MemoryReadError("implausible string length")
        return self.p.read(addr + STRING_CHARS, 2 * length).decode("utf-16-le", errors="replace")

    def class_name(self, klass: int) -> str:
        return self.p.cstring(self.p.ptr(klass + CLASS_NAME), 128)


class Obj:
    """A managed object; obj["m_field"] reads a field."""

    def __init__(self, mono: Mono, addr: int):
        self.mono = mono
        self.addr = addr
        self._klass: int | None = None

    @property
    def klass(self) -> int:
        if self._klass is None:  # looked up lazily: arrays are read without it
            self._klass = self.mono.p.ptr(self.mono.p.ptr(self.addr))  # MonoObject.vtable -> MonoVTable.klass
        return self._klass

    @property
    def class_name(self) -> str:
        return self.mono.class_name(self.klass)

    def __getitem__(self, name: str):
        field = self.mono.fields(self.klass).get(name)
        if field is None:
            raise MemoryReadError(f"{self.class_name} has no field {name}")
        return self.mono._value(self.addr + field.offset, field.type)

    def get(self, name: str, default=None):
        try:
            return self[name]
        except MemoryReadError:
            return default

    # Arrays and List<T>
    @property
    def length(self) -> int:
        length = self.mono.p.ptr(self.addr + ARRAY_LENGTH)
        if length > 100_000:
            raise MemoryReadError("implausible array length")
        return length

    def element_addr(self, index: int, size: int) -> int:
        return self.addr + ARRAY_DATA + index * size

    def array_refs(self) -> list[int]:
        n = self.length
        return list(struct.unpack(f"<{n}Q", self.mono.p.read(self.addr + ARRAY_DATA, 8 * n)))

    def array_ints(self) -> list[int]:
        n = self.length
        return list(struct.unpack(f"<{n}i", self.mono.p.read(self.addr + ARRAY_DATA, 4 * n)))

    def list_objects(self) -> list["Obj"]:
        """Items of a List<T> where T is a class."""
        size, items = self["_size"], self["_items"]
        if not items or size <= 0:
            return []
        return [Obj(self.mono, a) for a in items.array_refs()[:size] if a]

    def list_strings(self) -> list[str]:
        size, items = self["_size"], self["_items"]
        if not items or size <= 0:
            return []
        return [self.mono.string(a) for a in items.array_refs()[:size] if a]

    def list_ints(self) -> list[int]:
        size, items = self["_size"], self["_items"]
        if not items or size <= 0:
            return []
        return items.array_ints()[:size]
