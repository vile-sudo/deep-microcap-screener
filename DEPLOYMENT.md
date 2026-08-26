# Deployment guide

The app is one FastAPI service that serves both the API and the static
frontend, backed by SQLite by default. That means every option below is a
**single service to deploy** — no separate frontend hosting needed unless
you want it.

## Option A — Render.com (easiest, free tier available)

1. Push this repo to GitHub.
2. On Render: **New → Web Service**, connect the repo.
3. Settings:
   - **Root directory**: leave blank (repo root)
   - **Build command**: `pip install -r backend/requirements.txt && cd backend && python -m app.seed`
   - **Start command**: `cd backend && gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT`
4. Add a **persistent disk** (Render → Disks) mounted at `/opt/render/project/src/backend/data`
   if you want the SQLite file to survive redeploys. Otherwise every
   deploy re-seeds from the bundled JSON, which is also fine if your data
   only changes via `data/companies_raw.json` in git.
5. Deploy. Render gives you an HTTPS URL immediately.

## Option B — Railway

1. Push to GitHub, then **New Project → Deploy from repo** on Railway.
2. Railway auto-detects the `backend/Dockerfile`. If it doesn't, set:
   - **Dockerfile path**: `backend/Dockerfile`
   - **Docker build context**: `.` (repo root — the Dockerfile needs both
     `backend/` and `frontend/`)
3. Add a volume mounted at `/app/backend/data` for persistence.
4. Railway assigns a public URL automatically; add a custom domain under
   Settings if you want one.

## Option C — Fly.io

```bash
cd screener-app
fly launch --no-deploy         # generates fly.toml, choose a region near you
```

Edit the generated `fly.toml` so the build points at the repo root with
the backend Dockerfile:

```toml
[build]
  dockerfile = "backend/Dockerfile"

[[mounts]]
  source = "screener_data"
  destination = "/app/backend/data"
```

```bash
fly volumes create screener_data --size 1
fly deploy
```

## Option D — Your own VPS (systemd + nginx)

```bash
# on the server
git clone <your-repo> /opt/screener-app
cd /opt/screener-app/backend
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m app.seed
```

`/etc/systemd/system/screener.service`:

```ini
[Unit]
Description=Deep Microcap Screener
After=network.target

[Service]
WorkingDirectory=/opt/screener-app/backend
Environment=DATABASE_URL=sqlite:////opt/screener-app/backend/data/screener.db
ExecStart=/opt/screener-app/backend/.venv/bin/gunicorn app.main:app -k uvicorn.workers.UvicornWorker --bind 127.0.0.1:8000 --workers 2
Restart=always

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now screener
```

nginx reverse proxy (`/etc/nginx/sites-available/screener`):

```nginx
server {
    listen 80;
    server_name your-domain.com;
    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }
}
```

Then `certbot --nginx -d your-domain.com` for HTTPS.

## Switching to Postgres (recommended once more than one instance runs)

SQLite is fine for a single instance / low-traffic personal dashboard.
For anything with multiple app instances behind a load balancer, use
Postgres instead — no code changes needed:

```
DATABASE_URL=postgresql+psycopg2://user:password@host:5432/screener
```

(add `psycopg2-binary` to `backend/requirements.txt`), then re-run
`python -m app.seed` once against the new database.

## Keeping data fresh in production

`scripts/refresh_data.py` re-pulls quantitative fields (price, P/E, ROCE,
ROE, shareholding) from screener.in. Run it on a schedule — a cron job,
a scheduled CI job, or (if you're driving this from a Claude session) a
scheduled task that runs the refresh script and re-seeds:

```bash
python scripts/refresh_data.py && python -m app.seed
```

The qualitative research fields (business description, moat notes,
scores) are not touched by the refresh script — those are a research
step, documented in the README.

## Finding new candidates

`automation/` is a separate pipeline that runs on your own machine (Windows
Task Scheduler), not on Render — it scans NSE/BSE for new listings, sweeps
the small/mid-cap universe for names not on the board, and profiles what
prospectuses it can. See `automation/README.md` for setup. It writes
`backend/data/candidates_raw.json` and `build_stamp.json`, which is what
powers the dashboard's "Candidates queue" button — nothing it finds reaches
the board without a person deciding so.
