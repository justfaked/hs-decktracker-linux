"""Desktop app: the tracker in its own window, with a tray icon (Qt).

One process runs the tracker, its local web server and the window. `decktracker tray`
starts it with only the tray icon, `decktracker app` also opens the window. Starting
it again while it runs just opens the window of the running instance.
"""

import logging
import os
import signal
import socket
import sys
import threading
import webbrowser

from . import desktop
from .settings import Settings

APP_ID = desktop.APP_ID
APP_TITLE = "Deck Tracker"
STATUS_POLL_MS = 2000

log = logging.getLogger(__name__)


def _wayland() -> bool:
    return os.environ.get("XDG_SESSION_TYPE") == "wayland" or bool(os.environ.get("WAYLAND_DISPLAY"))


def free_port(preferred: int) -> int:
    """The preferred port if it's free (so bookmarks keep working), otherwise any free one."""
    for port in (preferred, 0):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
            except OSError:
                continue
            return s.getsockname()[1]
    raise OSError("no free port")


def status_text(state: dict) -> str:
    game = state.get("game") or {}
    if state["status"] == "playing":
        return f"{game['mode']} · turn {game['turn']}"
    if state["status"] == "finished":
        result = (game.get("me") or {}).get("result")
        return f"{game['mode']} · {'victory' if result == 'WON' else 'defeat' if result == 'LOST' else 'game over'}"
    if state["status"] == "drafting":
        draft = state.get("draft") or {}
        return f"Drafting {draft.get('cls_name') or ''} · {draft.get('count', 0)} cards".replace("  ", " ")
    if state["status"] == "unsupported":
        return f"{game.get('mode', 'Game')} (not tracked)"
    return "Waiting for a game"


def run(show_window: bool, hs_dir: str | None = None) -> int:
    try:
        from PySide6.QtCore import QByteArray, Qt, QTimer, QUrl
        from PySide6.QtGui import QAction, QIcon
        from PySide6.QtNetwork import QLocalServer, QLocalSocket
        from PySide6.QtWebEngineWidgets import QWebEngineView
        from PySide6.QtWidgets import QApplication, QMainWindow, QMenu, QMessageBox, QSystemTrayIcon
    except ImportError as exc:
        sys.exit(f"The desktop app needs PySide6 with QtWebEngine ({exc}).\n"
                 "Install it (e.g. `sudo dnf install python3-pyside6`) or use the browser version: "
                 "`python3 -m decktracker run --open`.")

    from .__main__ import build_app
    from .server import serve

    settings = Settings.load()
    # Wayland has no way for an app to keep its own window above others; X11 (XWayland) does.
    if settings.always_on_top and _wayland() and "QT_QPA_PLATFORM" not in os.environ:
        os.environ["QT_QPA_PLATFORM"] = "xcb"

    qt = QApplication(sys.argv)
    qt.setApplicationName(APP_ID)
    qt.setApplicationDisplayName(APP_TITLE)
    qt.setDesktopFileName(APP_ID)
    icon = QIcon(str(desktop.ICON_SOURCE))
    qt.setWindowIcon(icon)

    # Single instance: if one is already running, ask it to open its window.
    probe = QLocalSocket()
    probe.connectToServer(APP_ID)
    if probe.waitForConnected(300):
        if show_window:
            probe.write(b"show")
            probe.waitForBytesWritten(300)
        return 0
    QLocalServer.removeServer(APP_ID)
    ipc = QLocalServer()
    ipc.listen(APP_ID)

    try:
        tracker = build_app(hs_dir, read_memory=settings.read_memory)
        port = free_port(settings.port)
        server = serve(tracker, "127.0.0.1", port)
    except SystemExit as exc:  # e.g. Hearthstone not found
        QMessageBox.critical(None, APP_TITLE, str(exc.code))
        return 1
    threading.Thread(target=server.serve_forever, name="http", daemon=True).start()
    tracker.start()
    url = f"http://127.0.0.1:{port}/"
    log.info("tracker running at %s", url)

    class TrackerWindow(QMainWindow):
        def __init__(self):
            super().__init__()
            self.setWindowTitle(APP_TITLE)
            self.view = QWebEngineView(self)
            self.view.page().setBackgroundColor(Qt.GlobalColor.black)
            self.view.loadFinished.connect(
                lambda ok: None if ok else log.warning("the tracker page failed to load from %s", url))
            self.view.load(QUrl(url))
            self.setCentralWidget(self.view)
            self.resize(1100, 900)
            if settings.window_geometry:
                self.restoreGeometry(QByteArray.fromBase64(settings.window_geometry.encode()))
            self.apply_always_on_top()

        def apply_always_on_top(self) -> None:
            self.setWindowFlag(Qt.WindowType.WindowStaysOnTopHint, settings.always_on_top)

        def show_view(self, view: str) -> None:
            self.view.page().runJavaScript(f"showView({view!r})")
            self.bring_up()

        def bring_up(self) -> None:
            self.show()
            self.raise_()
            self.activateWindow()

        def closeEvent(self, event) -> None:
            settings.window_geometry = bytes(self.saveGeometry().toBase64()).decode()
            settings.save()
            if tray is not None:  # keep tracking in the background
                event.ignore()
                self.hide()
            else:
                event.accept()

    window = TrackerWindow()
    tray = None

    def toggle_memory(checked: bool) -> None:
        settings.read_memory = checked
        settings.save()
        tracker.set_read_memory(checked)

    def toggle_on_top(checked: bool) -> None:
        settings.always_on_top = checked
        settings.save()
        if _wayland() and qt.platformName() == "wayland":
            QMessageBox.information(
                window, APP_TITLE,
                "Always on top takes effect the next time the app starts.\n\n"
                "On KDE you can also right-click the window's title bar and choose "
                "More Actions → Keep Above Others.")
            return
        visible = window.isVisible()
        window.apply_always_on_top()
        if visible:
            window.show()

    def check(text: str, checked: bool, slot) -> QAction:
        action = QAction(text, checkable=True, checked=checked)
        action.toggled.connect(slot)
        return action

    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = QSystemTrayIcon(icon)
        menu = QMenu()
        menu.addAction("Open tracker", lambda: window.show_view("live"))
        menu.addAction("Stats", lambda: window.show_view("history"))
        menu.addAction("Open in browser", lambda: webbrowser.open(url))
        menu.addSeparator()
        actions = [
            check("Read draft offers (game memory)", settings.read_memory, toggle_memory),
            check("Always on top", settings.always_on_top, toggle_on_top),
            check("Start at login", desktop.autostart_enabled(), desktop.set_autostart),
        ]
        for action in actions:
            menu.addAction(action)
        menu.addSeparator()
        menu.addAction("Quit", qt.quit)
        tray.setContextMenu(menu)
        tray.activated.connect(
            lambda reason: window.bring_up() if reason == QSystemTrayIcon.ActivationReason.Trigger else None)
        tray.setToolTip(APP_TITLE)
        tray.show()

        def refresh_tooltip() -> None:
            tray.setToolTip(f"{APP_TITLE}: {status_text(tracker.snapshot())}")

        timer = QTimer()
        timer.timeout.connect(refresh_tooltip)
        timer.start(STATUS_POLL_MS)
    else:
        qt.setQuitOnLastWindowClosed(True)
        show_window = True

    if tray is not None:
        qt.setQuitOnLastWindowClosed(False)

    def on_connection() -> None:
        connection = ipc.nextPendingConnection()
        if connection.waitForReadyRead(300) and connection.readAll().data() == b"show":
            window.bring_up()

    ipc.newConnection.connect(on_connection)

    def shutdown() -> None:
        if window.isVisible():
            settings.window_geometry = bytes(window.saveGeometry().toBase64()).decode()
        settings.save()
        tracker.stop()
        server.shutdown()

    qt.aboutToQuit.connect(shutdown)
    # Quit cleanly on Ctrl+C / SIGTERM. Python only runs signal handlers between Qt events,
    # so a timer keeps the interpreter getting control.
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: qt.quit())
    heartbeat = QTimer()
    heartbeat.timeout.connect(lambda: None)
    heartbeat.start(300)
    if show_window:
        window.bring_up()
    return qt.exec()
