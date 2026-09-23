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
5. **Accounts need a real database.** On Render: **New → PostgreSQL**
   (the free plan is fine), open it, copy the **Internal Database URL**, and
   add it to the web service as `DATABASE_URL`. Also set `ADMIN_EMAIL` and
   `ADMIN_PASSWORD` (or keep your existing `AUTH_USERNAME` / `AUTH_PASSWORD`,
   which act as the admin login). Without Postgres, sign-ups and sessions are
   wiped on every deploy.
6. Deploy. Render gives you an HTTPS URL immediately.

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

## Option E — Your own AWS EC2 box + Hostinger domain (this project's setup)

This is Option D above, filled in with AWS/Hostinger specifics and wired
into the existing GitHub Actions workflows so every automated data update
(News Channel, Movers, deep-dive reports, ...) redeploys your box on every
push — via `git pull && docker compose up -d --build` over SSH, not a raw
systemd+venv setup. Skipped cleanly if `AWS_HOST`/`AWS_SSH_KEY` aren't set.

(This project ran AWS and Render in parallel for a while during the
initial cutover -- each workflow briefly had both a "Trigger Render
deploy" and a "Deploy to AWS" step. That's gone now that AWS is the one
live deployment; if you ever want a second target again, the SSH deploy
step below is the pattern to copy.)

### 1. On the EC2 instance

Security group: allow inbound **22** (SSH, ideally locked to your own IP),
**80** and **443** (HTTP/HTTPS, from anywhere) from the AWS console.

```bash
# SSH in, then:
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-plugin nginx certbot python3-certbot-nginx
sudo usermod -aG docker $USER && newgrp docker   # so `docker` works without sudo

sudo mkdir -p /opt/screener-app && sudo chown $USER /opt/screener-app
git clone https://github.com/vile-sudo/deep-microcap-screener /opt/screener-app
cd /opt/screener-app
cp .env.example .env
nano .env   # fill in ADMIN_EMAIL, ADMIN_PASSWORD, GH_DISPATCH_TOKEN, NEWSDATA_API_KEY, CRON_KEY -- whatever you use

docker compose up -d --build
curl localhost:8000/healthz   # should print {"status":"ok",...}
```

### 2. nginx + HTTPS for pkresearch.in

```bash
sudo cp deploy/nginx-pkresearch.in.conf /etc/nginx/sites-available/pkresearch.in
sudo ln -s /etc/nginx/sites-available/pkresearch.in /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
sudo certbot --nginx -d pkresearch.in -d www.pkresearch.in   # rewrites the site file to add HTTPS
```

### 3. Hostinger DNS

Hostinger → your domain → **DNS / Nameservers** → edit the **A record**:
point `pkresearch.in` (host `@`) and `www` at the EC2 instance's public IP
(use an **Elastic IP**, not the default public IP, so it survives a
stop/start). DNS can take a few minutes to a few hours to propagate —
until then the site is only reachable by the raw IP.

### 4. Wire up the SSH auto-deploy (GitHub Actions)

Every workflow that commits data (`daily.yml`, `news_channel.yml`,
`movers.yml`, `charts.yml`, etc.) now has a **"Deploy to AWS"** step that
SSHes in and runs `git pull --ff-only && docker compose up -d --build`.
It's a no-op until these repo secrets exist (**Settings → Secrets and
variables → Actions**):

```bash
# generate a deploy key with no passphrase (locally, not on the server)
ssh-keygen -t ed25519 -f deploy_key -N ""
# copy the PUBLIC half onto the server
ssh-copy-id -i deploy_key.pub ubuntu@<your-ec2-ip>
# (or: cat deploy_key.pub, paste it into ~/.ssh/authorized_keys on the box)
```

Then set:

| Secret | Value |
|---|---|
| `AWS_HOST` | the EC2 instance's IP or `pkresearch.in` once DNS is live |
| `AWS_SSH_KEY` | the *private* half, `cat deploy_key` — the whole file including the `BEGIN`/`END` lines |
| `AWS_SSH_USER` | optional, defaults to `ubuntu` (Amazon Linux uses `ec2-user`) |
| `AWS_DEPLOY_PATH` | optional, defaults to `/opt/screener-app` |

Delete `deploy_key`/`deploy_key.pub` locally once `AWS_SSH_KEY` is saved —
GitHub's secret store is now the only copy that matters.

### 5. Verify, then cut over

With both Render and AWS deploying on every push, open the EC2 box's IP
(or your domain once DNS resolves) directly and confirm it matches
Render's dashboard — same company count, same "Last fetched" times on
News Channel/Movers. Once you're confident:

- Point Hostinger's DNS fully at AWS (if you started with a subdomain for
  testing) and give it time to propagate.
- Remove the `RENDER_DEPLOY_HOOK` secret and the "Trigger Render deploy"
  step from every workflow (each one only has "Deploy to AWS" left now —
  see the note at the top of Option E) before you actually suspend or
  delete the Render service. Do this first: with the Render step still
  in place, a suspended Render's deploy hook returning an error would
  fail that step, and GitHub Actions skips every step after a failed one
  by default -- including "Deploy to AWS" right after it. Removing the
  Render step avoids that trap entirely.

Accounts created while both were live only exist on whichever database
each was pointed at — Render's Postgres and AWS's SQLite (per `.env.example`)
don't sync with each other. If you need existing sign-ups to carry over,
export them from Render's Postgres and import into the new database before
cutting over; the admin login itself (`ADMIN_EMAIL`/`ADMIN_PASSWORD`) is
recreated fresh on every startup regardless of database, so that one just
works on both.

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
