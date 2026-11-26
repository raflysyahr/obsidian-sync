import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_CONFIG = PROJECT_ROOT / "config" / "settings.json"

class Config:
    def __init__(self, path=DEFAULT_CONFIG):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save_defaults()
        self.data = self.load()

    def save_defaults(self):
        self.path.write_text(json.dumps({
            "vault_path": "",
            "remote_url": "",
            "branch": "main",
            "sync_mode": "manual_and_auto",
            "auto_sync_enabled": False,
            "auto_sync_interval_seconds": 300,
            "exclude": [".obsidian/workspace.json",
                        ".obsidian/workspace-mobile.json",
                        ".trash/", ".DS_Store", "Thumbs.db"],
            "backup_before_pull": True
        }, indent=2) + "\n", encoding="utf-8")

    def load(self):
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError(f"Cannot read config {self.path}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError("Settings must contain a JSON object.")
        return data

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2) + "\n",
                             encoding="utf-8")

    def set(self, key, value):
        self.data[key] = value
        self.save()
