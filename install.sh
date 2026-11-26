#!/data/data/com.termux/files/usr/bin/bash
set -e
ROOT="$(cd "$(dirname "$0")" && pwd)"
pkg install -y python git
python -c 'import curses, json, pathlib' || {
  echo "Python curses module is unavailable in this Termux environment."
  exit 1
}
chmod +x "$ROOT/main.py"
echo
echo "Installation checks passed."
echo "Start with: cd \"$ROOT\" && python main.py"
