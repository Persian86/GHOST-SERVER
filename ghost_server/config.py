"""Paths and process-level settings. Everything a user changes lives in the
database (settings table) and is edited from the admin page, not here."""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = Path(os.environ.get("GHOST_DATA", "/data")).resolve()
PORT = int(os.environ.get("GHOST_PORT", "8780"))
FILES_DIR = DATA_DIR / "files"
DB_PATH = DATA_DIR / "ghost.db"
# Where an Obsidian vault is mounted into the container (optional).
VAULT_DIR = Path(os.environ.get("GHOST_VAULT", str(DATA_DIR / "vault")))


def ensure_dirs() -> None:
    for d in (DATA_DIR, FILES_DIR):
        d.mkdir(parents=True, exist_ok=True)
