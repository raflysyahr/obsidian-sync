import subprocess
import tempfile
import unittest
from pathlib import Path

from core.dashboard_status import build_dashboard_snapshot


class DashboardStatusTests(unittest.TestCase):
    def test_unconfigured_vault_has_safe_defaults(self):
        snapshot = build_dashboard_snapshot({})
        self.assertEqual(snapshot["repository"], "Not configured")
        self.assertEqual(snapshot["changes"], None)
        self.assertEqual(snapshot["last_sync_status"], "No sync yet")

    def test_non_repository_is_reported_without_mutation(self):
        with tempfile.TemporaryDirectory() as folder:
            snapshot = build_dashboard_snapshot({"vault_path": folder})
            self.assertEqual(snapshot["repository"], "Not initialized")
            self.assertFalse((Path(folder) / ".git").exists())

    def test_repository_snapshot_includes_branch_remote_and_changes(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            subprocess.run(["git", "init", "--initial-branch", "main"], cwd=root,
                           check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            (root / "note.md").write_text("hello\n", encoding="utf-8")
            snapshot = build_dashboard_snapshot({
                "vault_path": folder,
                "auto_sync_enabled": True,
                "auto_sync_interval_seconds": 600,
                "last_sync_at": "2026-11-26 10:00:00",
                "last_sync_status": "Success",
            })
            self.assertEqual(snapshot["repository"], "Ready")
            self.assertEqual(snapshot["branch"], "main")
            self.assertEqual(snapshot["changes"], 1)
            self.assertTrue(snapshot["auto_enabled"])
            self.assertEqual(snapshot["interval"], 600)
            self.assertEqual(snapshot["last_sync_status"], "Success")


if __name__ == "__main__":
    unittest.main()
