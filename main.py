#!/usr/bin/env python3
from core.environment import check_environment
from core.config import Config
from core.logger import setup_logging
from ui.app import run_app

def main():
    setup_logging()
    problems = check_environment()
    if problems:
        print("Obsidian Sync - environment check failed:")
        for problem in problems:
            print(f" - {problem}")
        raise SystemExit(1)
    config = Config()
    run_app(config)

if __name__ == "__main__":
    main()
