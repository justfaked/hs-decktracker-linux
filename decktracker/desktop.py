"""App menu entry, icon and login autostart (freedesktop.org desktop files)."""

import os
import shutil
import sys
from pathlib import Path

APP_ID = "decktracker"
ICON_SOURCE = Path(__file__).parent / "web" / "icon.svg"

ENTRY = """[Desktop Entry]
Type=Application
Name={name}
GenericName=Hearthstone Deck Tracker
Comment=Track your Hearthstone deck, Arena drafts and stats
Exec={command} {subcommand}
Icon={app_id}
Terminal=false
Categories=Game;Utility;
Keywords=hearthstone;deck;tracker;arena;
StartupWMClass={app_id}
"""


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")


def config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")


def app_entry_path() -> Path:
    return data_home() / "applications" / f"{APP_ID}.desktop"


def autostart_path() -> Path:
    return config_home() / "autostart" / f"{APP_ID}.desktop"


def icon_path() -> Path:
    return data_home() / "icons" / "hicolor" / "scalable" / "apps" / f"{APP_ID}.svg"


def _quote(arg: str) -> str:
    """Quote an Exec= argument per the desktop entry spec."""
    if not any(c in arg for c in ' \t"\'\\$`'):
        return arg
    return '"' + arg.replace("\\", "\\\\").replace('"', '\\"').replace("$", "\\$").replace("`", "\\`") + '"'


def command() -> str:
    """How the desktop should start the tracker: the installed command, or this checkout."""
    installed = shutil.which(APP_ID)
    if installed:
        return _quote(installed)
    checkout = Path(__file__).resolve().parent.parent
    return " ".join(_quote(a) for a in ("env", f"PYTHONPATH={checkout}", sys.executable, "-m", APP_ID))


def entry(name: str, subcommand: str) -> str:
    return ENTRY.format(name=name, command=command(), subcommand=subcommand, app_id=APP_ID)


def autostart_enabled() -> bool:
    return autostart_path().exists()


def set_autostart(enabled: bool) -> None:
    path = autostart_path()
    if enabled:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(entry("Deck Tracker (tray)", "tray"), encoding="utf-8")
    else:
        path.unlink(missing_ok=True)


def install(autostart: bool = True) -> list[Path]:
    """Add the app menu entry and icon (and start the tray icon at login)."""
    app = app_entry_path()
    app.parent.mkdir(parents=True, exist_ok=True)
    app.write_text(entry("Deck Tracker", "app"), encoding="utf-8")
    icon = icon_path()
    icon.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ICON_SOURCE, icon)
    written = [app, icon]
    if autostart:
        set_autostart(True)
        written.append(autostart_path())
    return written


def uninstall() -> list[Path]:
    removed = []
    for path in (app_entry_path(), icon_path(), autostart_path()):
        if path.exists():
            path.unlink()
            removed.append(path)
    return removed
