"""Pure helpers for the terminal UI (no curses import, so they are unit-testable)."""
import re
import textwrap

MIN_INTERVAL = 60
MAX_INTERVAL = 86400
LABEL_WIDTH = 8
NOT_CONFIGURED = "(not configured)"


def shorten(text, width, where="middle"):
    """Fit text into width columns using an ellipsis (middle keeps both ends of a path)."""
    text = str(text)
    if width <= 0:
        return ""
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    if where == "end":
        return text[:width - 1] + "…"
    keep = width - 1
    left = keep // 3
    return text[:left] + "…" + text[len(text) - (keep - left):]


def mask_url(url):
    """Hide credentials embedded in a URL (https://user:token@host -> https://***@host)."""
    return re.sub(r"(://)[^/@\s]+@", r"\1***@", str(url))


def format_duration(seconds):
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    if seconds < 3600:
        minutes, rest = divmod(seconds, 60)
        return f"{minutes}m {rest:02d}s" if rest else f"{minutes}m"
    hours, rest = divmod(seconds, 3600)
    minutes = rest // 60
    return f"{hours}h {minutes:02d}m" if minutes else f"{hours}h"


def parse_interval(raw):
    """Parse '300', '300s', '5m' or '1h'. Returns (seconds, error)."""
    match = re.fullmatch(r"\s*(\d+)\s*([smh]?)\s*", str(raw).lower())
    if not match:
        return None, "Use a number such as 300, 5m or 1h."
    seconds = int(match.group(1)) * {"": 1, "s": 1, "m": 60, "h": 3600}[match.group(2)]
    if not MIN_INTERVAL <= seconds <= MAX_INTERVAL:
        return None, "Interval must be between 1 minute and 24 hours."
    return seconds, None


def validate_remote(url):
    """Mirror GitEngine.set_remote rules so mistakes are caught while typing."""
    url = str(url).strip()
    if not url:
        return "Enter a repository URL."
    if any(ch.isspace() for ch in url) or url.startswith("-"):
        return "The URL cannot contain spaces."
    if url.startswith("https://"):
        if re.match(r"https://[^/]*@", url):
            return "Remove the username/token from the URL."
        return None
    if url.startswith("ssh://") or (":" in url and "@" in url):
        return None
    return "Use an HTTPS URL, ssh:// URL or git@host:user/repo.git"


def wrap_lines(text, width):
    """Split text on newlines (also literal backslash-n) and wrap each line."""
    width = max(4, int(width))
    lines = []
    for part in str(text).replace("\\n", "\n").split("\n"):
        lines.extend(textwrap.wrap(part, width, replace_whitespace=False,
                                   drop_whitespace=True) or [""])
    return lines


_TIPS = [
    (("could not read username", "authentication failed", "permission denied (publickey)",
      "invalid username or password", "terminal prompts disabled"),
     "Git login is not set up yet. Configure a credential helper or SSH key in Termux."),
    (("author identity unknown", "please tell me who you are", "unable to auto-detect email"),
     'Set your Git identity once: git config --global user.name "Your Name" and '
     'git config --global user.email "you@example.com"'),
    (("diverged",),
     "Both sides changed. Resolve it manually with git, then try again."),
    (("local changes",),
     "Press C to commit your changes first, then pull again."),
    (("remote branch could not be fetched",),
     "The remote branch does not exist yet. Press P to push first."),
    (("'origin' is not configured", "remote 'origin'"),
     "Press R to set the GitHub remote."),
    (("not a git repository", "initialize the local repository"),
     "Press I to initialize Git for this Vault."),
    (("configure the vault path",),
     "Press V to choose your Vault folder."),
    (("could not resolve host", "network is unreachable", "unable to access", "timed out"),
     "Check your internet connection and try again."),
]


def explain_error(text):
    lowered = str(text).lower()
    for needles, tip in _TIPS:
        if any(needle in lowered for needle in needles):
            return tip
    return ""


def next_step(view):
    """Return (hotkey, text, style) for the most useful next action, or None."""
    if view["vault"] == NOT_CONFIGURED:
        return ("V", "Start here: choose your Vault folder", "accent")
    repo = view["repository"]
    if repo == "Checking…":
        return None
    if repo == "Not initialized":
        return ("I", "Next: initialize Git for this Vault", "accent")
    if repo != "Ready":
        return ("V", "Vault problem: check the folder path", "warn")
    if view["remote"] == NOT_CONFIGURED:
        return ("R", "Next: add your GitHub repository URL", "accent")
    changes = view["changes"] or 0
    never = view["last_sync_at"] in ("", "Never")
    if never:
        if changes:
            return ("C", "First time: commit your notes, then push", "accent")
        return ("P", "Next: push to publish your Vault to GitHub", "accent")
    if changes:
        word = "change" if changes == 1 else "changes"
        return ("N", f"{changes} unsynced {word}: press N to sync", "accent")
    return ("N", "Up to date. Press N to sync again", "ok")


def build_status_lines(view, width):
    """Dashboard status rows as (priority, [(text, style), ...]); lower priority = keep first."""
    avail = max(10, width - 6 - LABEL_WIDTH)

    def label(name):
        return (name.ljust(LABEL_WIDTH), "dim")

    configured = view["vault"] != NOT_CONFIGURED
    lines = [(4, [label("Vault"),
                  (shorten(view["vault"], avail), "text" if configured else "warn")])]

    repo = view["repository"]
    repo_style = {"Ready": "ok", "Checking…": "dim", "Unavailable": "err"}.get(repo, "warn")
    git = [label("Git"), ("● ", repo_style), (repo, repo_style)]
    if repo == "Ready":
        git.append((f" · {view['branch']}", "text"))
        changes = view["changes"]
        if changes == 0:
            git.append((" · clean", "ok"))
        elif changes:
            git.append((f" · {changes} change{'s' if changes != 1 else ''}", "warn"))
    lines.append((1, git))

    remote = view["remote"]
    if remote == NOT_CONFIGURED:
        lines.append((3, [label("Remote"), ("not set", "warn")]))
    else:
        lines.append((3, [label("Remote"), (shorten(mask_url(remote), avail), "text")]))

    if view["auto_enabled"]:
        auto = [label("Auto"), ("ON", "ok"),
                (f" · every {format_duration(view['interval'])}", "text")]
        if view.get("next_in"):
            auto.append((f" · next in {view['next_in']}", "dim"))
    else:
        auto = [label("Auto"), ("OFF", "dim")]
    lines.append((2, auto))

    status = view["last_sync_status"]
    status_style = {"Success": "ok", "Error": "err"}.get(status, "dim")
    if view["last_sync_at"] in ("", "Never"):
        last = [label("Last"), ("never synced", "dim")]
    else:
        last = [label("Last"), (view["last_sync_at"] + " ", "text"), (f"[{status}]", status_style)]
    lines.append((5, last))
    if status == "Error" and view["last_sync_message"]:
        lines.append((6, [label(""), (shorten(view["last_sync_message"], avail, "end"), "err")]))
    return lines


def fit_status_lines(lines, max_rows):
    """Keep the highest-priority rows that fit, preserving display order."""
    if max_rows <= 0:
        return []
    keep = sorted(range(len(lines)), key=lambda i: lines[i][0])[:max_rows]
    return [lines[i][1] for i in sorted(keep)]
