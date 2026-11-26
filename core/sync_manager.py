"""Manual commit, push, and conservative fast-forward pull operations."""
import fnmatch
import subprocess
import zipfile
from datetime import datetime
from pathlib import Path


class SyncError(RuntimeError):
    pass


class SyncManager:
    def __init__(self, vault_path, branch="main", exclude=None, timeout=120):
        self.vault = Path(vault_path).expanduser().resolve()
        self.branch = branch or "main"
        self.exclude = list(exclude or [])
        self.timeout = timeout
        if not self.vault.is_dir():
            raise SyncError("Vault folder is missing or is not a directory.")
        root = self._run("rev-parse", "--show-toplevel").stdout.strip()
        if Path(root).resolve() != self.vault:
            raise SyncError("The Vault must be the root of its own Git repository.")

    def _run(self, *args, allowed=(0,)):
        try:
            result = subprocess.run(
                ["git", *args], cwd=str(self.vault), text=True,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                timeout=self.timeout, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise SyncError("Git operation timed out.") from exc
        except OSError as exc:
            raise SyncError(f"Cannot run Git: {exc}") from exc
        if result.returncode not in allowed:
            detail = (result.stderr or result.stdout or "Unknown Git error").strip()
            raise SyncError(detail)
        return result

    def _require_remote(self):
        result = self._run("remote", "get-url", "origin", allowed=(0, 2, 128))
        if result.returncode != 0 or not result.stdout.strip():
            raise SyncError("GitHub remote 'origin' is not configured.")

    def commit(self, message):
        message = message.strip()
        if not message or "\n" in message or "\r" in message:
            raise SyncError("Enter a non-empty, single-line commit message.")
        pathspec = ["."] + [f":(exclude){item}" for item in self.exclude if item]
        self._run("add", "-A", "--", *pathspec)
        changed = self._run("diff", "--cached", "--quiet", allowed=(0, 1))
        if changed.returncode == 0:
            return "No staged changes to commit."
        self._run("commit", "-m", message)
        return "Local commit created successfully."

    def push(self):
        self._require_remote()
        branch = self._run("branch", "--show-current").stdout.strip()
        if not branch:
            raise SyncError("Cannot push from a detached HEAD.")
        self._run("push", "--set-upstream", "origin", branch)
        return f"Pushed branch '{branch}' to origin."

    def _has_head(self):
        return self._run("rev-parse", "--verify", "HEAD", allowed=(0, 128)).returncode == 0

    def _is_excluded(self, path):
        path = path.replace("\\", "/").lstrip("./")
        for pattern in self.exclude:
            pattern = str(pattern).replace("\\", "/").lstrip("./")
            if not pattern:
                continue
            if pattern.endswith("/") and path.startswith(pattern):
                return True
            if fnmatch.fnmatchcase(path, pattern):
                return True
        return False

    def _create_backup(self):
        backup_dir = self.vault.parent / ".obsidian-sync-backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        archive = backup_dir / f"{self.vault.name}-{stamp}.zip"
        try:
            with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
                for path in self.vault.rglob("*"):
                    relative = path.relative_to(self.vault)
                    if relative.parts and relative.parts[0] == ".git":
                        continue
                    if path.is_file() and not path.is_symlink():
                        bundle.write(path, relative.as_posix())
        except Exception as exc:
            try:
                archive.unlink(missing_ok=True)
            except OSError:
                pass
            raise SyncError(f"Backup failed; pull cancelled: {exc}") from exc
        return archive

    def pull(self, backup=True):
        self._require_remote()
        current_branch = self._run("branch", "--show-current").stdout.strip()
        if current_branch != self.branch:
            raise SyncError(
                f"Switch to configured branch '{self.branch}' before pulling (current: '{current_branch or 'detached HEAD'}')."
            )
        status = self._run("status", "--porcelain=v1", "--untracked-files=all").stdout
        blocking = []
        for line in status.splitlines():
            if len(line) < 4:
                continue
            changed_path = line[3:].strip().strip('"')
            if not self._is_excluded(changed_path):
                blocking.append(changed_path)
        if blocking:
            raise SyncError("Vault has local changes. Commit or otherwise resolve them before pulling.")
        self._run("fetch", "origin", self.branch)
        fetched = self._run("rev-parse", "--verify", "FETCH_HEAD", allowed=(0, 128))
        if fetched.returncode != 0:
            raise SyncError("Remote branch could not be fetched.")
        if self._has_head():
            remote_is_ancestor = self._run(
                "merge-base", "--is-ancestor", "FETCH_HEAD", "HEAD", allowed=(0, 1)
            ).returncode == 0
            if remote_is_ancestor:
                return "Already up to date (or local branch is ahead); nothing pulled."
            local_is_ancestor = self._run(
                "merge-base", "--is-ancestor", "HEAD", "FETCH_HEAD", allowed=(0, 1)
            ).returncode == 0
            if not local_is_ancestor:
                raise SyncError("Local and remote histories diverged. Pull stopped; manual conflict resolution is required.")
        backup_path = self._create_backup() if backup else None
        self._run("merge", "--ff-only", "FETCH_HEAD")
        if backup_path:
            return f"Fast-forward pull completed. Backup: {backup_path}"
        return "Fast-forward pull completed."
    def auto_sync(self, backup=True):
        """Commit Vault edits, fast-forward from origin, then push safely.

        This deliberately never performs a merge commit, rebase, force-push, or
        destructive reset. Divergent histories stop with an error.
        """
        status = self._run("status", "--porcelain=v1", "--untracked-files=all").stdout
        if status.strip():
            stamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            commit_result = self.commit(f"Obsidian Sync auto-sync {stamp}")
        else:
            commit_result = "No uncommitted Vault changes."

        # Pull first so a remote-only fast-forward is incorporated before push.
        # If local and remote both advanced, pull's divergence guard stops here.
        pull_result = self.pull(backup=backup)
        push_result = self.push()
        return f"{commit_result}\n{pull_result}\n{push_result}"
