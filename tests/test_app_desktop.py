import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from decktracker import desktop
from decktracker.app import App
from decktracker.gui import free_port, status_text
from decktracker.history import History
from decktracker.hsmemory import DraftState
from decktracker.settings import Settings
from tests.helpers import CARDS


class SettingsTest(unittest.TestCase):
    def test_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            self.assertEqual(Settings.load(path), Settings())
            Settings(read_memory=True, port=9000).save(path)
            self.assertEqual(Settings.load(path), Settings(read_memory=True, port=9000))

    def test_ignores_unknown_keys_and_broken_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            path.write_text('{"read_memory": true, "from_the_future": 1}')
            self.assertTrue(Settings.load(path).read_memory)
            path.write_text("{not json")
            self.assertEqual(Settings.load(path), Settings())


class DesktopTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        env = {"XDG_DATA_HOME": f"{self.tmp.name}/data", "XDG_CONFIG_HOME": f"{self.tmp.name}/config"}
        self.env = mock.patch.dict(os.environ, env)
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_install_and_remove(self):
        with mock.patch("shutil.which", return_value="/home/me/.local/bin/decktracker"):
            written = desktop.install()
        self.assertEqual(len(written), 3)
        entry = desktop.app_entry_path().read_text()
        self.assertIn("Exec=/home/me/.local/bin/decktracker app\n", entry)
        self.assertIn("Icon=decktracker\n", entry)
        self.assertIn("Exec=/home/me/.local/bin/decktracker tray\n", desktop.autostart_path().read_text())
        self.assertTrue(desktop.icon_path().read_text().startswith("<svg"))
        self.assertEqual(len(desktop.uninstall()), 3)
        self.assertFalse(desktop.autostart_enabled())

    def test_autostart_toggle_and_checkout_command(self):
        with mock.patch("shutil.which", return_value=None):
            desktop.set_autostart(True)
            text = desktop.autostart_path().read_text()
        self.assertIn("-m decktracker tray", text)
        self.assertIn("PYTHONPATH=", text)
        desktop.set_autostart(False)
        self.assertFalse(desktop.autostart_enabled())

    def test_exec_quoting(self):
        self.assertEqual(desktop._quote("/usr/bin/python3"), "/usr/bin/python3")
        self.assertEqual(desktop._quote("/home/a b/x"), '"/home/a b/x"')
        self.assertEqual(desktop._quote('say "$hi"'), '"say \\"\\$hi\\""')


class FakeMemory:
    def __init__(self):
        self.closed = threading.Event()

    def connect(self):
        return True

    def draft_state(self):
        return DraftState(mode="DRAFTING", slot="CARD", underground=False, choices=[])

    def close(self):
        self.closed.set()


class MemoryToggleTest(unittest.TestCase):
    def wait_for(self, condition, timeout=3.0):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if condition():
                return True
            time.sleep(0.02)
        return False

    def test_enable_and_disable_at_runtime(self):
        memories = []

        def factory():
            memories.append(FakeMemory())
            return memories[-1]

        with tempfile.TemporaryDirectory() as tmp:
            app = App(Path(tmp), CARDS, History(":memory:"), memory_factory=factory)
            self.assertFalse(app.read_memory)
            self.assertFalse(app.snapshot()["desktop"])  # the page's hint points to --read-memory
            app.set_read_memory(True)
            self.assertTrue(self.wait_for(lambda: app.tracker.memory_status == "ok"))
            app.set_read_memory(False)
            self.assertFalse(app.read_memory)
            self.assertIsNone(app.tracker.memory_status)
            self.assertTrue(memories[0].closed.wait(3))
            app.stop()


class GuiHelpersTest(unittest.TestCase):
    def test_free_port_falls_back_when_taken(self):
        with socket.socket() as busy:
            busy.bind(("127.0.0.1", 0))
            busy.listen()
            taken = busy.getsockname()[1]
            port = free_port(taken)
        self.assertNotEqual(port, taken)
        self.assertGreater(port, 0)

    def test_status_text(self):
        self.assertEqual(status_text({"status": "idle"}), "Waiting for a game")
        playing = {"status": "playing", "game": {"mode": "Ranked", "turn": 7}}
        self.assertEqual(status_text(playing), "Ranked · turn 7")
        drafting = {"status": "drafting", "draft": {"cls_name": "Mage", "count": 12}}
        self.assertEqual(status_text(drafting), "Drafting Mage · 12 cards")


if __name__ == "__main__":
    unittest.main()
