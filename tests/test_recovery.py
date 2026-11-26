import tempfile
import unittest
from pathlib import Path
from core.recovery import (
    create_backup, verify_backup, restore_to_new_directory,
    RecoveryError, security_report,
)


class RecoveryTests(unittest.TestCase):
    def test_backup_verify_and_restore(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            vault = base / "Vault"
            vault.mkdir()
            (vault / "note.md").write_text("# Hello\\n", encoding="utf-8")
            (vault / ".git").mkdir()
            (vault / ".git" / "config").write_text("ignored", encoding="utf-8")
            archive = create_backup(vault)
            info = verify_backup(archive)
            self.assertEqual(info["files"], 1)
            destination = base / "Recovered"
            restore_to_new_directory(archive, destination)
            self.assertEqual((destination / "note.md").read_text(), "# Hello\\n")
            self.assertFalse((destination / ".git").exists())

    def test_restore_refuses_nonempty_destination(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            vault = base / "Vault"
            vault.mkdir()
            (vault / "note.md").write_text("note", encoding="utf-8")
            archive = create_backup(vault)
            destination = base / "Existing"
            destination.mkdir()
            (destination / "keep.txt").write_text("keep", encoding="utf-8")
            with self.assertRaises(RecoveryError):
                restore_to_new_directory(archive, destination)
            self.assertEqual((destination / "keep.txt").read_text(), "keep")

    def test_tampered_archive_fails_integrity_check(self):
        with tempfile.TemporaryDirectory() as temp:
            base = Path(temp)
            vault = base / "Vault"
            vault.mkdir()
            (vault / "note.md").write_text("original", encoding="utf-8")
            archive = create_backup(vault)
            import zipfile
            tampered = base / "tampered.zip"
            with zipfile.ZipFile(archive) as source, zipfile.ZipFile(tampered, "w") as target:
                for name in source.namelist():
                    data = source.read(name)
                    if name == "note.md":
                        data = b"changed"
                    target.writestr(name, data)
            with self.assertRaises(RecoveryError):
                verify_backup(tampered)

    def test_security_report_never_claims_token_storage(self):
        report = security_report("")
        self.assertTrue(any("does not store GitHub tokens" in item for item in report))


if __name__ == "__main__":
    unittest.main()
