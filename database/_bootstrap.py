"""Shared start-up for the seeding and verification scripts.

They live outside `backend/`, so they have to put the application package on
the import path before they can reuse its connection code. Doing that here,
once, keeps the top of every script down to a single import.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BACKEND_DIR = PROJECT_ROOT / "backend"
DATA_DIR = Path(os.getenv("JM_DATA_DIR") or (PROJECT_ROOT / "data"))

if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))


def setup(verbose: bool = True) -> None:
    """Logging, and the .env file if python-dotenv is installed."""
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO" if verbose else "WARNING").upper(),
        format="%(levelname)-7s %(name)s | %(message)s",
    )
    try:
        from dotenv import load_dotenv          # type: ignore
    except ImportError:
        return
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        load_dotenv(env_file, override=False)


def city_dir() -> Path:
    """The bundled study-area bundle the graph is seeded from."""
    return DATA_DIR / "city" / os.getenv("JM_CITY", "bengaluru_south")


def mobility_dir() -> Path:
    """The bundled enterprise booking history."""
    return DATA_DIR / "mobility"


def banner(title: str) -> None:
    print()
    print("=" * 74)
    print(title)
    print("=" * 74)
