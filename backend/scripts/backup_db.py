"""Consistent copy of the live SQLite database, run INSIDE the app container.

    docker compose exec -T screener python - < backend/scripts/backup_db.py

Writes /tmp/backup.db (SQLite's online-backup API, safe while the site is serving),
checks it, and prints one summary line. .github/workflows/backup_db.yml runs it
nightly over SSH and carries the file off the server. Exit codes: 0 fine, 2 the copy
failed its integrity check, 3 the app is not on SQLite (nothing to do here).
"""
import sqlite3
import sys

sys.path.insert(0, "/app/backend")
from app.config import get_settings  # noqa: E402

url = get_settings().sqlalchemy_url
if not url.startswith("sqlite"):
    print("backup_db: the app is not on SQLite; this script has nothing to copy")
    sys.exit(3)
path = url.split("///", 1)[1]

src = sqlite3.connect(path)
dst = sqlite3.connect("/tmp/backup.db")
src.backup(dst)
src.close()
ok = dst.execute("PRAGMA integrity_check").fetchone()[0]
counts = {}
for table in ("users", "watchlist_items", "saved_filters", "companies"):
    try:
        counts[table] = dst.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    except sqlite3.Error:
        counts[table] = "?"
dst.close()
print(f"backup_db: integrity={ok} " + " ".join(f"{k}={v}" for k, v in counts.items()))
sys.exit(0 if ok == "ok" else 2)
