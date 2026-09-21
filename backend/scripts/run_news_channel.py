"""
Fetch China/India/USA monopoly-sector news and write
backend/data/news_channel/latest.json.

    cd backend
    python scripts/run_news_channel.py

Run by .github/workflows/news_channel.yml on a schedule, and on demand via
POST /api/admin/news-channel/run-now. See app/news_channel.py for the two
sources (Google News RSS, always; newsdata.io, only when
NEWSDATA_API_KEY is set) and the rules behind each.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from app import news_channel  # noqa: E402
from app.config import get_settings  # noqa: E402


def main() -> int:
    key = get_settings().newsdata_api_key
    payload = news_channel.write(key)
    msg = f"news channel: {payload['count']} items"
    if payload.get("errors"):
        msg += f"; errors: {payload['errors']}"
    print(msg)
    return 0


if __name__ == "__main__":
    sys.exit(main())
