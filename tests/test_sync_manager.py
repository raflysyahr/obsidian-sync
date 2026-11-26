import subprocess
import tempfile
import unittest
from pathlib import Path

from core.sync_manager import SyncManager, SyncError


def git(path, *args):
    return subprocess.run(["git", *args], cwd=path, check=True,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)


class SyncManagerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.vault = self.root / "vault"
        self.remote = self.root / "remote.git"
        self.vault.mkdir()
        git(self.root, "init", "--bare", str(self.remote))
        git(self.vault, "init", "--initial-branch", "main")
        git(self.vault, "config", "user.name", "Test User")
        git(self.vault, "config", "user.email", "test@example.invalid")
        git(self.vault, "remote", "add", "origin", str(self.remote))
        (self.vault / "note.md").write_text("first\n", encoding="utf-8")
        git(self.vault, "add", "note.md")
        git(self.vault, "commit", "-m", "initial")
        git(self.vault, "push", "-u", "origin", "main")
        self.manager = SyncManager(self.vault, "main", [".obsidian/workspace.json"])

    def tearDown(self):
        self.tmp.cleanup()

    def test_commit_stages_vault_changes_and_excludes_workspace(self):
        (self.vault / "note.md").write_text("updated\n", encoding="utf-8")
        (self.vault / ".obsidian").mkdir()
        (self.vault / ".obsidian/workspace.json").write_text("volatile", encoding="utf-8")
        result = self.manager.commit("update note")
        self.assertIn("created", result)
        changed = git(self.vault, "show", "--pretty=", "--name-only", "HEAD").stdout
        self.assertIn("note.md", changed)
        self.assertNotIn("workspace.json", changed)

    def test_push_sends_local_commit_to_origin(self):
        (self.vault / "pushed.md").write_text("push me\n", encoding="utf-8")
        self.manager.commit("prepare push")
        result = self.manager.push()
        self.assertIn("Pushed branch 'main'", result)
        remote_log = git(self.root, "--git-dir", str(self.remote), "log", "--oneline", "-1", "refs/heads/main").stdout
        self.assertIn("prepare push", remote_log)

    def test_pull_refuses_dirty_worktree(self):
        (self.vault / "note.md").write_text("local edits\n", encoding="utf-8")
        with self.assertRaises(SyncError):
            self.manager.pull()

    def test_pull_fast_forwards_and_creates_backup(self):
        clone = self.root / "clone"
        git(self.root, "clone", "--branch", "main", str(self.remote), str(clone))
        git(clone, "config", "user.name", "Remote User")
        git(clone, "config", "user.email", "remote@example.invalid")
        (clone / "remote.md").write_text("from remote\n", encoding="utf-8")
        git(clone, "add", "remote.md")
        git(clone, "commit", "-m", "remote update")
        git(clone, "push", "origin", "main")
        result = self.manager.pull()
        self.assertIn("Backup:", result)
        self.assertTrue((self.vault / "remote.md").exists())
        backups = list((self.root / ".obsidian-sync-backups").glob("*.zip"))
        self.assertEqual(len(backups), 1)


if __name__ == "__main__":
    unittest.main()
