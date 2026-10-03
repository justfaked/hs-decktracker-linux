"""Locating Hearthstone inside Wine/Proton prefixes and preparing its log.config."""

import re
from pathlib import Path

HS_SUBDIRS = ("Program Files (x86)/Hearthstone", "Program Files/Hearthstone")

# Where Proton/Wine prefixes usually live on Linux (Steam, Steam flatpak, Lutris, Bottles, Heroic).
PREFIX_GLOBS = (
    ".local/share/Steam/steamapps/compatdata/*/pfx",
    ".steam/steam/steamapps/compatdata/*/pfx",
    ".var/app/com.valvesoftware.Steam/.local/share/Steam/steamapps/compatdata/*/pfx",
    "Games/*",
    "Games/*/*",
    ".wine",
    ".local/share/bottles/bottles/*",
    ".var/app/com.usebottles.bottles/data/bottles/bottles/*",
    ".var/app/com.heroicgameslauncher.hgl/config/heroic/prefixes/*",
)

# Log sections the tracker needs. Verbose Power logging carries card IDs.
REQUIRED_SECTIONS = {
    "Power": {"LogLevel": "1", "FilePrinting": "True", "ConsolePrinting": "False",
              "ScreenPrinting": "False", "Verbose": "True"},
    "Decks": {"LogLevel": "1", "FilePrinting": "True", "ConsolePrinting": "False",
              "ScreenPrinting": "False", "Verbose": "False"},
    "Arena": {"LogLevel": "1", "FilePrinting": "True", "ConsolePrinting": "False",
              "ScreenPrinting": "False", "Verbose": "True"},
    "LoadingScreen": {"LogLevel": "1", "FilePrinting": "True", "ConsolePrinting": "False",
                      "ScreenPrinting": "False", "Verbose": "False"},
}


def find_installs(home: Path | None = None) -> list[Path]:
    home = home or Path.home()
    found = []
    for pattern in PREFIX_GLOBS:
        for prefix in home.glob(pattern):
            for sub in HS_SUBDIRS:
                hs = prefix / "drive_c" / sub
                if (hs / "Hearthstone.exe").exists():
                    found.append(hs.resolve())
    # Most recently used install first.
    return sorted(set(found), key=lambda p: _mtime(p / "Logs"), reverse=True)


def _mtime(path: Path) -> float:
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def log_config_path(install: Path) -> Path | None:
    """%LOCALAPPDATA%/Blizzard/Hearthstone/log.config inside the same prefix."""
    drive_c = next((p for p in install.parents if p.name == "drive_c"), None)
    if drive_c is None:
        return None
    users = sorted((drive_c / "users").glob("*/AppData/Local/Blizzard/Hearthstone"))
    preferred = [u for u in users if "steamuser" in u.parts] or users
    if preferred:
        return preferred[0] / "log.config"
    return drive_c / "users" / "steamuser" / "AppData" / "Local" / "Blizzard" / "Hearthstone" / "log.config"


def parse_log_config(text: str) -> dict[str, dict[str, str]]:
    sections: dict[str, dict[str, str]] = {}
    current = None
    for line in text.splitlines():
        line = line.strip()
        if m := re.match(r"^\[(.+)\]$", line):
            current = sections.setdefault(m[1], {})
        elif current is not None and "=" in line:
            key, value = line.split("=", 1)
            current[key.strip()] = value.strip()
    return sections


def render_log_config(sections: dict[str, dict[str, str]], newline: str = "\r\n") -> str:
    out = []
    for name, values in sections.items():
        out.append(f"[{name}]")
        out += [f"{k}={v}" for k, v in values.items()]
    return newline.join(out) + newline


def ensure_log_config(path: Path) -> list[str]:
    """Add/fix the log sections the tracker needs. Returns the names of changed sections."""
    original = path.read_bytes().decode("utf-8-sig") if path.exists() else ""
    existing = parse_log_config(original)
    newline = "\n" if original and "\r\n" not in original else "\r\n"
    changed = []
    for name, required in REQUIRED_SECTIONS.items():
        section = existing.setdefault(name, {})
        if any(section.get(k) != v for k, v in required.items()):
            section.update(required)
            changed.append(name)
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            backup = path.with_name("log.config.bak")
            if not backup.exists():
                backup.write_bytes(path.read_bytes())
        path.write_bytes(render_log_config(existing, newline).encode("utf-8"))
    return changed


def detect_locale(session_dir: Path) -> str | None:
    """The client language, from 'SetLocale: enUS' in Hearthstone.log."""
    try:
        with (session_dir / "Hearthstone.log").open(encoding="utf-8", errors="replace") as f:
            for i, line in enumerate(f):
                if m := re.search(r"SetLocale: (\w{4})", line):
                    return m[1]
                if i > 2000:
                    break
    except OSError:
        pass
    return None
