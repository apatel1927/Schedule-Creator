#!/usr/bin/env python3
"""Create a consistent backup of the live SQLite database to /data/backups."""
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import os

data = Path(os.environ.get("DATA_DIR", "/data"))
source = data / "shiftledger.sqlite3"
directory = data / "backups"
directory.mkdir(parents=True, exist_ok=True)
target = directory / ("shiftledger-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + ".sqlite3")
with sqlite3.connect(source) as existing, sqlite3.connect(target) as backup:
    existing.backup(backup)
    assert backup.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
print(f"Verified backup: {target}")
