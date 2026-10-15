"""Nightly: run india_pead_screener on the app's data and publish its SQLite for the calculators.

    cd backend
    python scripts/run_pead_engine.py               # fetch last night's database, run, publish
    python scripts/run_pead_engine.py --no-publish  # local run only

The previous database is fetched first so its point-in-time history (sue_history) keeps growing; scores
are recomputed in full each night. Publishing needs `gh` with GH_TOKEN (the workflow's token).
"""
from __future__ import annotations

import argparse
import gzip
import shutil
import subprocess
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))
from app import pead_store as S  # noqa: E402


def _gh(*args) -> subprocess.CompletedProcess:
    return subprocess.run(["gh", *args], capture_output=True, text=True)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--no-publish", action="store_true")
    a = ap.parse_args()
    S.DB_FILE.parent.mkdir(parents=True, exist_ok=True)
    if not a.no_publish:
        d = S.DB_FILE.parent / "dl"
        shutil.rmtree(d, ignore_errors=True)
        if _gh("release", "download", S.RELEASE, "--pattern", S.ASSET, "--dir", str(d)).returncode == 0:
            with gzip.open(d / S.ASSET, "rb") as src, open(S.DB_FILE, "wb") as dst:
                shutil.copyfileobj(src, dst)
            print("pead engine: continuing last night's database")
        shutil.rmtree(d, ignore_errors=True)
    eng = BACKEND_DIR / "india_pead_screener"
    r = subprocess.run([sys.executable, str(eng / "main.py"), "--config", str(eng / "config_app.yaml"), "--source", "app"])
    if r.returncode != 0:
        print("pead engine: the run failed - not publishing")
        return 1
    if a.no_publish:
        return 0
    gz = S.DB_FILE.parent / S.ASSET
    with open(S.DB_FILE, "rb") as src, gzip.open(gz, "wb", compresslevel=6) as dst:
        shutil.copyfileobj(src, dst)
    if _gh("release", "view", S.RELEASE).returncode != 0:
        _gh("release", "create", S.RELEASE, "--title", "PEAD engine database",
            "--notes", "SRWD SUE and EAR scores for Indian results (backend/india_pead_screener), rebuilt nightly.")
    up = _gh("release", "upload", S.RELEASE, str(gz), "--clobber")
    print(f"pead engine: published {gz.stat().st_size / 1e6:.1f} MB" if up.returncode == 0 else f"pead engine: publish failed {up.stderr[-300:]}")
    gz.unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
