"""Safe file helpers for the Markdown editor (atomic writes, backups, note listing)."""
import os
import shutil
import tempfile
from datetime import datetime
from pathlib import Path

MAX_BYTES = 1_000_000
NOTE_SUFFIXES = (".md", ".markdown", ".txt", ".text")
SKIP_DIRS = {"node_modules", "__pycache__"}


class NoteError(RuntimeError):
    pass


def read_note(path):
    """Return (text, newline). Refuses huge or non-UTF-8 files instead of corrupting them."""
    path = Path(path)
    if not path.is_file():
        raise NoteError(f"File not found: {path}")
    if path.stat().st_size > MAX_BYTES:
        raise NoteError("File is larger than 1 MB; open a smaller note.")
    raw = path.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise NoteError("This file is not UTF-8 text.") from exc
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, newline


def write_note(path, text, newline="\n"):
    """Write atomically so a crash never leaves a half-written note."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = text.replace("\r\n", "\n").replace("\n", newline)
    fd, tmp = tempfile.mkstemp(prefix=".tmp-", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            handle.write(data)
        if path.exists():
            shutil.copymode(path, tmp)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def backup_before_overwrite(path, vault=None):
    """Copy an existing note into <vault parent>/.obsidian-sync-backups/edits/."""
    path = Path(path)
    if not path.is_file():
        return None
    base = Path(vault).parent if vault else path.parent
    folder = base / ".obsidian-sync-backups" / "edits"
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{path.stem}-{datetime.now():%Y%m%d-%H%M%S}{path.suffix}"
    shutil.copy2(path, target)
    return target


def list_notes(vault, limit=400):
    """Newest-first list of note files, skipping hidden folders such as .git/.obsidian."""
    found = []
    for folder, dirs, files in os.walk(vault):
        dirs[:] = [d for d in dirs if not d.startswith(".") and d not in SKIP_DIRS]
        for name in files:
            if name.lower().endswith(NOTE_SUFFIXES) and not name.startswith("."):
                full = Path(folder) / name
                try:
                    found.append((full.stat().st_mtime, full))
                except OSError:
                    continue
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _, path in found[:limit]]


def suggest_save_path(path):
    """Plain-text sources are saved as a NEW .md file; the original stays untouched."""
    path = Path(path)
    if path.suffix.lower() in (".txt", ".text"):
        target = path.with_suffix(".md")
        if target.exists():
            target = path.with_name(path.stem + ".formatted.md")
        return target
    return path
