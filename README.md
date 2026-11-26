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

## Safety
Stage 1 does not run Git mutations, upload files, or access the network. Git sync and token handling will be added in later stages. Do not put GitHub tokens in `settings.json`.
