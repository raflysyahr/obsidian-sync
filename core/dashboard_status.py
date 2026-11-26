"""Read-only information used by the Obsidian Sync dashboard."""
from core.git_engine import GitEngine, GitError


def build_dashboard_snapshot(settings):
    """Return a compact dashboard snapshot without changing the Vault."""
    vault = str(settings.get("vault_path", "") or "")
    snapshot = {
        "vault": vault or "(not configured)",
        "repository": "Not configured" if not vault else "Not initialized",
        "branch": str(settings.get("branch", "main") or "main"),
        "remote": "(not configured)",
        "changes": None,
        "auto_enabled": bool(settings.get("auto_sync_enabled", False)),
        "interval": max(60, int(settings.get("auto_sync_interval_seconds", 300))),
        "last_sync_at": str(settings.get("last_sync_at", "Never")),
        "last_sync_status": str(settings.get("last_sync_status", "No sync yet")),
        "last_sync_message": str(settings.get("last_sync_message", "")),
    }
    if not vault:
        return snapshot
    try:
        status = GitEngine(vault).status()
    except GitError as exc:
        detail = str(exc)
        if "not a Git repository" in detail:
            snapshot["repository"] = "Not initialized"
        else:
            snapshot["repository"] = "Unavailable"
            snapshot["last_sync_message"] = detail
        return snapshot
    snapshot.update({
        "repository": "Ready",
        "branch": status["branch"],
        "remote": status["remote"] or "(not configured)",
        "changes": len(status["changes"]),
    })
    return snapshot
