import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, Mock

from core.git_engine import GitEngine, GitError


class GitEngineTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.vault = Path(self.tmp.name)
        self.engine = GitEngine(self.vault)

    def tearDown(self):
        self.tmp.cleanup()

    @patch("core.git_engine.subprocess.run")
    def test_initialize_uses_argument_list_and_does_not_commit(self, run):
        run.side_effect = [
            Mock(returncode=128, stdout="", stderr="not a git repository"),
            Mock(returncode=0, stdout="", stderr=""),
        ]
        result = self.engine.initialize("main")
        self.assertIn("No files were committed", result)
        self.assertEqual(run.call_args.args[0], ["git", "init", "--initial-branch", "main"])
        self.assertFalse(any("commit" in call.args[0] for call in run.call_args_list))

    def test_rejects_invalid_branch(self):
        with self.assertRaises(GitError):
            self.engine.initialize("--upload-pack=bad")

    def test_rejects_non_git_remote_scheme(self):
        with self.assertRaises(GitError):
            self.engine.set_remote("file:///tmp/repo")

    def test_rejects_credentials_in_https_remote(self):
        with self.assertRaises(GitError):
            self.engine.set_remote("https://user:token@github.com/user/repo.git")


if __name__ == "__main__":
    unittest.main()
