import shutil
import sys

def check_environment():
    problems = []
    if sys.version_info < (3, 9):
        problems.append("Python 3.9 or newer is required.")
    if not shutil.which("git"):
        problems.append("Git CLI not found. Install it with: pkg install git")
    try:
        import curses  # noqa: F401
    except ImportError:
        problems.append("Python curses is unavailable in this environment.")
    return problems
