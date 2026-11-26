"""Safe, small Git CLI wrapper for a single Obsidian Vault."""
import subprocess
from pathlib import Path
from urllib.parse import urlsplit


class GitError(RuntimeError):
    pass


class GitEngine:
    def __init__(self, vault_path, timeout=20):
        self.vault = Path(vault_path).expanduser().resolve()
        self.timeout = timeout
        if not self.vault.exists() or not self.vault.is_dir():
            raise GitError("Vault folder is missing or is not a directory.")

    def _run(self, *args):
        try:
            result = subprocess.run(
                ["git", *args], cwd=str(self.vault), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise GitError("Git command timed out.") from exc
        except OSError as exc:
            raise GitError(f"Cannot run Git: {exc}") from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "Unknown Git error").strip()
            raise GitError(detail)
        return result.stdout.strip()

    def is_repository(self):
        try:
            root = self._run("rev-parse", "--show-toplevel")
        except GitError:
            return False
        return Path(root).resolve() == self.vault

    def repository_root(self):
        try:
            root = self._run("rev-parse", "--show-toplevel")
        except GitError:
            return None
        return Path(root).resolve()

    def initialize(self, branch="main"):
        if self.repository_root():
            if self.repository_root() != self.vault:
                raise GitError("This Vault is inside another Git repository. Refusing to modify it.")
            return "Repository already initialized."
        if not branch or branch.startswith("-") or any(c.isspace() for c in branch):
            raise GitError("Invalid branch name.")
        self._run("init", "--initial-branch", branch)
        return f"Initialized local repository on branch '{branch}'. No files were committed."

    def status(self):
        if not self.is_repository():
            raise GitError("This Vault is not a Git repository yet.")
        branch = self._run("branch", "--show-current") or "(detached HEAD)"
        porcelain = self._run("status", "--porcelain=v1", "--untracked-files=all")
        entries = []
        for line in porcelain.splitlines():
            if len(line) >= 4:
                entries.append({"code": line[:2], "path": line[3:]})
        remote = self._run("remote", "get-url", "origin") if self._has_origin() else ""
        return {"branch": branch, "changes": entries, "remote": remote}

    def _has_origin(self):
        try:
            self._run("remote", "get-url", "origin")
            return True
        except GitError:
            return False

    def set_remote(self, url):
        if not self.is_repository():
            raise GitError("Initialize the local repository first.")
        url = url.strip()
        if not (url.startswith("https://") or url.startswith("ssh://") or
                (":" in url and "@" in url and not url.startswith("-"))):
            raise GitError("Use an HTTPS or SSH Git remote URL.")
        if any(ch.isspace() for ch in url) or url.startswith("-"):
            raise GitError("Remote URL contains unsupported characters.")
        if url.startswith("https://"):
            parsed = urlsplit(url)
            if parsed.username or parsed.password:
                raise GitError("Do not put a username, password, or token in the remote URL.")
        if self._has_origin():
            self._run("remote", "set-url", "origin", url)
            return "Updated origin remote URL."
        self._run("remote", "add", "origin", url)
        return "Added origin remote URL."
