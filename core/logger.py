import logging
from pathlib import Path
from core.config import PROJECT_ROOT

def setup_logging():
    log_dir = PROJECT_ROOT / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        filename=log_dir / "obsidian-sync.log",
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s"
    )
    return logging.getLogger("obsidian_sync")
