"""Command line entry point: `python3 -m decktracker [run|setup]`."""

import argparse
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


def cmd_run(args) -> None:
    install = resolve_install(args.hs_dir)
    log.info("Hearthstone: %s", install)
    check_log_config(install)

    locale = args.locale
    if locale == "auto":
        sessions = session_dirs(install / "Logs")
        locale = (paths.detect_locale(sessions[-1]) if sessions else None) or "enUS"
    cards = CardDB.load(locale)
    log.info("card data: %d cards (%s)", len(cards), locale)

    ratings = Ratings.load()
    log.info("arena ratings: %d entries (HearthArena)", len(ratings))
    app = App(install, cards, History(args.db), ratings)
    server = serve(app, args.host, args.port)
    url = f"http://{'localhost' if args.host in ('127.0.0.1', '0.0.0.0') else args.host}:{args.port}/"
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
    parser.add_argument("--hs-dir", help="Hearthstone install directory (auto-detected by default)")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command")

    run = sub.add_parser("run", help="start the tracker (default)")
    sub.add_parser("setup", help="enable the Hearthstone logs the tracker needs")
    for p in (parser, run):
        p.add_argument("--host", default="127.0.0.1", help="bind address (0.0.0.0 to view from other devices)")
        p.add_argument("--port", type=int, default=8765)
        p.add_argument("--locale", default="auto", help="card language, e.g. enUS or deDE (default: game's)")
        p.add_argument("--db", default=None, help="history database path")
        p.add_argument("--open", action="store_true", help="open the page in the default browser")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(asctime)s %(levelname)s %(message)s", datefmt="%H:%M:%S")
    if args.command == "setup":
        cmd_setup(args)
    else:
        cmd_run(args)


if __name__ == "__main__":
    main()
