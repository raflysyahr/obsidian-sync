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


## Stage 4 — Auto Sync

Auto-sync can be enabled from the TUI. The default interval is 300 seconds;
valid intervals are 60–86400 seconds. On each scheduled run, the app commits
non-excluded Vault changes, attempts a safe fast-forward pull, then pushes the
current branch. A divergent history or Git/network/authentication error stops
the cycle; no force-push, reset, rebase, or automatic conflict resolution is
performed. The first remote publication should be done manually with Stage 3
Push before enabling auto-sync. Keep the app running for scheduled syncs.


## Stage 6: Security & Recovery
- Create ZIP backups of the Vault (excluding `.git`).
- Verify backup file hashes before restore.
- Restore only to a new or empty destination; the configured Vault is never overwritten.
- Security check warns about embedded credentials in the Git remote URL and broad settings-file permissions.
- GitHub tokens are not stored by this application.


## Using the dashboard
The dashboard guides you: the line under the title always suggests the next step
(for example "Next: add your GitHub repository URL") and the menu starts on it.

| Key | Action |
|---|---|
| `↑` `↓` / `j` `k`, `Enter` | Move and open an item |
| Yellow letter (`V` `I` `R` `S` `C` `P` `L` `N` `A` `T` `B` `F` `O` `U` `E` `H`) | Jump to that item and run it |
| `?` | Help and first-time workflow |
| `M` | Turn touch/mouse support on or off (off keeps native copy/paste) |
| `Q` | Quit |

- **Touch / mouse:** tap an item to select it, tap again to open it; swipe to scroll.
  Mouse support is switched off automatically while you type so long-press paste keeps working.
- **Typing:** `Enter` saves, `Esc` cancels without changing anything, `Ctrl+U` clears the line.
  Fields are pre-filled with the current value or a sensible suggestion.
- **Sync now (`N`)** commits, pulls (fast-forward only) and pushes in one step.
- Long operations show a spinner; errors include a short tip on how to fix them.
- Scheduled auto-sync never interrupts you with a pop-up: the result appears on the dashboard.
- The terminal needs at least 30x10 characters; the layout adapts to small phone screens.
