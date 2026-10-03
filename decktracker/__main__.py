"""Command line entry point: `python3 -m decktracker [run|setup]`."""

import argparse
import errno
import logging
import subprocess
import sys
from pathlib import Path

from . import paths
from .app import App
from .cards import CardDB
from .history import History
from .logfile import session_dirs
from .ratings import Ratings
from .server import serve

log = logging.getLogger("decktracker")


def resolve_install(arg: str | None) -> Path:
    if arg:
        install = Path(arg).expanduser()
        if not (install / "Hearthstone.exe").exists():
            sys.exit(f"Hearthstone.exe not found in {install}")
        return install
    installs = paths.find_installs()
    if not installs:
        sys.exit("Could not find a Hearthstone install in any Wine/Proton prefix; pass --hs-dir.")
    return installs[0]


def cmd_setup(args) -> None:
    install = resolve_install(args.hs_dir)
    print(f"Hearthstone: {install}")
    config = paths.log_config_path(install)
    if config is None:
        sys.exit("Could not locate the Wine prefix's drive_c.")
    changed = paths.ensure_log_config(config)
    if changed:
        print(f"Updated {config}\n  enabled: {', '.join(changed)}")
        print("Restart Hearthstone for the change to take effect.")
    else:
        print(f"{config} already has everything the tracker needs.")


def check_log_config(install: Path) -> None:
    config = paths.log_config_path(install)
    if config is None:
        return
    current = paths.parse_log_config(config.read_text(encoding="utf-8-sig")) if config.exists() else {}
    missing = [name for name, req in paths.REQUIRED_SECTIONS.items()
               if any(current.get(name, {}).get(k) != v for k, v in req.items())]
    if missing:
        log.warning("log.config is missing %s; run `python3 -m decktracker setup` and restart Hearthstone",
                    ", ".join(missing))


def cmd_setup_desktop(args) -> None:
    from . import desktop
    if args.remove:
        removed = desktop.uninstall()
        print("Removed:\n  " + "\n  ".join(map(str, removed)) if removed else "Nothing to remove.")
        return
    for path in desktop.install(autostart=not args.no_autostart):
        print(f"Wrote {path}")
    print(f"\"Deck Tracker\" is now in your app menu (runs: {desktop.command()} app).")
    if not args.no_autostart:
        print("Its tray icon starts at login, so games are recorded even when the window is closed.")


def build_app(hs_dir: str | None, locale: str = "auto", db: str | None = None,
              read_memory: bool = False) -> App:
    """Load card data and ratings and set up the tracker (not started yet)."""
    install = resolve_install(hs_dir)
    log.info("Hearthstone: %s", install)
    check_log_config(install)
    if locale == "auto":
        sessions = session_dirs(install / "Logs")
        locale = (paths.detect_locale(sessions[-1]) if sessions else None) or "enUS"
    cards = CardDB.load(locale)
    log.info("card data: %d cards (%s)", len(cards), locale)
    ratings = Ratings.load()
    log.info("arena ratings: %d entries (HearthArena)", len(ratings))
    return App(install, cards, History(db), ratings, read_memory=read_memory)


def cmd_run(args) -> None:
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
    app = build_app(args.hs_dir, args.locale, args.db, args.read_memory)
    try:
        server = serve(app, args.host, args.port)
    except OSError as exc:
        if exc.errno != errno.EADDRINUSE:
            raise
        log.error("port %d is already in use; is the tracker (or the tracker app) already running? "
                  "Try %s or pass --port", args.port, url)
        if args.open:
            subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        sys.exit(1)
    app.start()
    log.info("tracker running at %s", url)
    if args.open:
        subprocess.Popen(["xdg-open", url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        app.stop()
        server.server_close()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="decktracker", description="Hearthstone deck tracker for Linux/Proton")
    # Options work before or after the subcommand; subcommands don't override
    # values given at the top level, hence SUPPRESS defaults there.
    def common(p: argparse.ArgumentParser, top: bool) -> None:
        d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
        p.add_argument("--hs-dir", default=d(None), help="Hearthstone install directory (auto-detected by default)")
        p.add_argument("-v", "--verbose", action="store_true", default=d(False), help="debug logging")

    def run_options(p: argparse.ArgumentParser, top: bool) -> None:
        d = (lambda v: v) if top else (lambda v: argparse.SUPPRESS)
        p.add_argument("--host", default=d("127.0.0.1"), help="bind address (0.0.0.0 to view from other devices)")
        p.add_argument("--port", type=int, default=d(8765), help="port of the web page (default 8765)")
        p.add_argument("--locale", default=d("auto"), help="card language, e.g. enUS or deDE (default: game's)")
        p.add_argument("--db", default=d(None), help="history database path")
        p.add_argument("--open", action="store_true", default=d(False), help="open the page in the default browser")
        p.add_argument("--read-memory", action="store_true", default=d(False),
                       help="read the Arena draft offer from Hearthstone's memory (read-only, opt-in)")

    common(parser, True)
    run_options(parser, True)
    sub = parser.add_subparsers(dest="command")
    run = sub.add_parser("run", help="start the tracker (default)")
    common(run, False)
    run_options(run, False)
    setup = sub.add_parser("setup", help="enable the Hearthstone logs the tracker needs")
    common(setup, False)
    for name, text in (("app", "open the tracker window (desktop app with tray icon)"),
                       ("tray", "start the desktop app with only its tray icon")):
        common(sub.add_parser(name, help=text), False)
    desk = sub.add_parser("setup-desktop", help="add the app to the app menu and start it at login")
    desk.add_argument("--no-autostart", action="store_true", help="don't start the tray icon at login")
    desk.add_argument("--remove", action="store_true", help="remove the menu entry, icon and autostart")
    common(desk, False)

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.command == "setup":
        cmd_setup(args)
    elif args.command in ("app", "tray"):
        from . import gui
        sys.exit(gui.run(show_window=args.command == "app", hs_dir=args.hs_dir))
    elif args.command == "setup-desktop":
        cmd_setup_desktop(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
