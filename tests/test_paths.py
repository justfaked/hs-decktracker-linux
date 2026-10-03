import tempfile
import unittest
from pathlib import Path

from decktracker import paths

ORIGINAL = "[Power]\r\nLogLevel=1\r\nFilePrinting=True\r\nVerbose=False\r\n[Achievements]\r\nLogLevel=1\r\n"


class LogConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "log.config"

    def tearDown(self):
        self.tmp.cleanup()

    def test_adds_missing_sections_and_keeps_others(self):
        self.path.write_bytes(ORIGINAL.encode())
        changed = paths.ensure_log_config(self.path)
        self.assertIn("Decks", changed)
        self.assertIn("Power", changed)  # Verbose was off
        text = self.path.read_bytes().decode()
        config = paths.parse_log_config(text)
        self.assertEqual(config["Power"]["Verbose"], "True")
        self.assertEqual(config["Achievements"], {"LogLevel": "1"})
        self.assertNotIn("\n", text.replace("\r\n", ""))  # line endings preserved
        self.assertEqual((self.path.parent / "log.config.bak").read_bytes().decode(), ORIGINAL)

    def test_idempotent(self):
        paths.ensure_log_config(self.path)
        before = self.path.read_bytes()
        self.assertEqual(paths.ensure_log_config(self.path), [])
        self.assertEqual(self.path.read_bytes(), before)

    def test_log_config_path_in_prefix(self):
        root = Path(self.tmp.name)
        install = root / "pfx/drive_c/Program Files (x86)/Hearthstone"
        install.mkdir(parents=True)
        (root / "pfx/drive_c/users/steamuser/AppData/Local/Blizzard/Hearthstone").mkdir(parents=True)
        self.assertEqual(
            paths.log_config_path(install),
            root / "pfx/drive_c/users/steamuser/AppData/Local/Blizzard/Hearthstone/log.config",
        )


if __name__ == "__main__":
    unittest.main()
