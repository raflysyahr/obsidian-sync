import json
import tempfile
import unittest
from pathlib import Path
from core.config import Config

class ConfigTests(unittest.TestCase):
    def test_creates_default_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            config = Config(path)
            self.assertTrue(path.exists())
            self.assertEqual(config.data["branch"], "main")
            self.assertFalse(config.data["auto_sync_enabled"])

    def test_persists_setting(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "settings.json"
            config = Config(path)
            config.set("branch", "develop")
            self.assertEqual(json.loads(path.read_text())["branch"], "develop")

if __name__ == "__main__":
    unittest.main()
