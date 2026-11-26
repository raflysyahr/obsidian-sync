# Obsidian Sync

A small Python TUI foundation for syncing one Obsidian Vault with GitHub from Termux.

## Requirements
- Termux
- Python 3.9+
- Git CLI

## Install
```bash
pkg update
pkg install python git
termux-setup-storage
unzip obsidian-sync-stage1.zip
cd obsidian-sync
bash install.sh
python main.py
```

In the dashboard, choose **Configure Vault path** and enter the absolute path to your Vault.
Common Android shared-storage paths look like:
`/storage/emulated/0/Documents/Obsidian/MyVault`

The exact path depends on where the Vault is stored. Grant Termux storage permission when prompted.

## Tests
```bash
python -m unittest discover -s tests -v
```

## Stage 2 and Stage 3
Stage 2 adds local repository initialization, status inspection, and `origin` remote configuration.
Stage 3 adds manual local commits, push, and conservative pull operations.

- **Commit local changes** stages Vault changes while respecting the configured `exclude` list.
- **Push to GitHub** pushes the current branch to `origin`; authentication is handled by Git itself.
- **Pull from GitHub** refuses to run with non-excluded local changes and only allows fast-forward updates.
- Before a pull that will update files, the app creates a ZIP backup beside the Vault in `.obsidian-sync-backups/` (when `backup_before_pull` is enabled).
- Diverged histories are not auto-merged; resolve them manually before trying again.

Do not put GitHub tokens in `settings.json` or in a remote URL. Configure Git authentication separately in Termux. Auto-sync is not enabled by this stage.
