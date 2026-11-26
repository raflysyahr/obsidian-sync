"""Verified Vault backups and non-destructive restore to a new directory."""
import hashlib
import json
import os
import stat
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath


class RecoveryError(RuntimeError):
    pass


MANIFEST_NAME = "obsidian-sync-manifest.json"


def _sha256(path):
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def create_backup(vault_path, backup_dir=None):
    vault = Path(vault_path).expanduser().resolve()
    if not vault.is_dir():
        raise RecoveryError("Vault folder is missing or is not a directory.")
    target_dir = Path(backup_dir).expanduser().resolve() if backup_dir else (
        vault.parent / ".obsidian-sync-backups"
    )
    target_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
    archive = target_dir / f"{vault.name}-{stamp}.zip"
    entries = {}
    try:
        with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
            for item in sorted(vault.rglob("*")):
                relative = item.relative_to(vault)
                if not relative.parts or relative.parts[0] == ".git":
                    continue
                if item.is_symlink():
                    continue
                if item.is_file():
                    rel = relative.as_posix()
                    entries[rel] = {"sha256": _sha256(item), "size": item.stat().st_size}
                    bundle.write(item, rel)
            manifest = {
                "format": 1,
                "created_at": datetime.now().astimezone().isoformat(timespec="seconds"),
                "vault_name": vault.name,
                "files": entries,
            }
            bundle.writestr(MANIFEST_NAME, json.dumps(manifest, indent=2) + "\n")
    except Exception as exc:
        try:
            archive.unlink(missing_ok=True)
        except OSError:
            pass
        raise RecoveryError(f"Backup failed: {exc}") from exc
    return archive


def verify_backup(archive_path):
    archive = Path(archive_path).expanduser().resolve()
    if not archive.is_file():
        raise RecoveryError("Backup archive does not exist.")
    try:
        with zipfile.ZipFile(archive, "r") as bundle:
            names = bundle.namelist()
            if names.count(MANIFEST_NAME) != 1:
                raise RecoveryError("Backup manifest is missing or duplicated.")
            manifest = json.loads(bundle.read(MANIFEST_NAME).decode("utf-8"))
            if manifest.get("format") != 1 or not isinstance(manifest.get("files"), dict):
                raise RecoveryError("Unsupported or invalid backup manifest.")
            expected = manifest["files"]
            actual_names = set(names) - {MANIFEST_NAME}
            if actual_names != set(expected):
                raise RecoveryError("Backup file list does not match its manifest.")
            for name, metadata in expected.items():
                path = PurePosixPath(name)
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    raise RecoveryError(f"Unsafe path in backup: {name}")
                data = bundle.read(name)
                digest = hashlib.sha256(data).hexdigest()
                if digest != metadata.get("sha256") or len(data) != metadata.get("size"):
                    raise RecoveryError(f"Integrity check failed: {name}")
    except (OSError, zipfile.BadZipFile, KeyError, ValueError, UnicodeError) as exc:
        if isinstance(exc, RecoveryError):
            raise
        raise RecoveryError(f"Cannot verify backup: {exc}") from exc
    return {"archive": str(archive), "files": len(expected),
            "created_at": manifest.get("created_at", "unknown")}


def restore_to_new_directory(archive_path, destination):
    """Restore only into a new/empty directory; never overwrite an existing Vault."""
    info = verify_backup(archive_path)
    target = Path(destination).expanduser().resolve()
    if target.exists():
        if not target.is_dir() or any(target.iterdir()):
            raise RecoveryError("Restore destination must not exist or must be empty.")
    else:
        target.mkdir(parents=True, exist_ok=False)
    try:
        with zipfile.ZipFile(info["archive"], "r") as bundle:
            for name in bundle.namelist():
                if name == MANIFEST_NAME:
                    continue
                relative = PurePosixPath(name)
                if relative.is_absolute() or ".." in relative.parts:
                    raise RecoveryError(f"Unsafe archive path: {name}")
                output = target.joinpath(*relative.parts)
                output.parent.mkdir(parents=True, exist_ok=True)
                # Files are written from verified archive bytes, not extractall().
                output.write_bytes(bundle.read(name))
    except Exception as exc:
        # Keep partial recovery visible for manual inspection; never delete user data.
        raise RecoveryError(f"Restore stopped; inspect partial destination {target}: {exc}") from exc
    return target


def list_backups(vault_path):
    vault = Path(vault_path).expanduser().resolve()
    folder = vault.parent / ".obsidian-sync-backups"
    if not folder.is_dir():
        return []
    return sorted((p for p in folder.glob("*.zip") if p.is_file()),
                  key=lambda p: p.stat().st_mtime, reverse=True)


def security_report(vault_path, config_path=None):
    """Return actionable security warnings without reading credential contents."""
    from urllib.parse import urlsplit
    warnings = []
    vault = Path(vault_path).expanduser().resolve() if vault_path else None
    if vault and vault.is_dir():
        git_config = vault / ".git" / "config"
        if git_config.is_file():
            try:
                content = git_config.read_text(encoding="utf-8", errors="replace")
                for line in content.splitlines():
                    if line.strip().startswith("url = "):
                        candidate = line.split("=", 1)[1].strip()
                        parsed = urlsplit(candidate)
                        if parsed.username or parsed.password:
                            warnings.append("Git remote URL appears to contain embedded credentials. Remove them and rotate that token/password.")
                            break
            except OSError:
                warnings.append("Could not inspect Git remote configuration.")
    if config_path:
        path = Path(config_path)
        if path.exists():
            try:
                mode = stat.S_IMODE(path.stat().st_mode)
                if mode & 0o077:
                    warnings.append("Settings file is accessible by group/other users; restrict its permissions where supported.")
            except OSError:
                warnings.append("Could not inspect settings-file permissions.")
    warnings.append("Obsidian Sync does not store GitHub tokens. Use Git's interactive authentication; never paste tokens into remote URLs or chat.")
    return warnings
