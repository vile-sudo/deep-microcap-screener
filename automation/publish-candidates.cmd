@echo off
REM publish-candidates.cmd — ship the candidates queue to the live dashboard.
REM
REM   publish-candidates.cmd                     confirm, then commit and push with a dated message
REM   publish-candidates.cmd "why this changed"   same, with your own commit message
REM   publish-candidates.cmd -y "message"         no confirmation prompt
REM
REM What "publishing" actually means here
REM   Render builds this app from a Docker image and re-seeds its database from
REM   backend/data/*.json on every deploy (see backend/Dockerfile). So shipping
REM   a candidates-queue update is: push backend/data/candidates_raw.json and
REM   backend/data/build_stamp.json to GitHub, then tell Render to rebuild.
REM
REM   Unlike the Netlify setup this project's earlier board used, this Render
REM   service is connected as a "Public Git Repository," which does NOT
REM   auto-deploy on push (Render's GitHub App integration wasn't reachable
REM   from the sandbox that first deployed this — see DEPLOYMENT.md). So a
REM   plain `git push` alone changes nothing live. This script pushes AND
REM   triggers the redeploy, via a Render Deploy Hook URL you create once:
REM
REM     Render dashboard -> deep-microcap-screener -> Settings -> Deploy Hook
REM     -> Create Deploy Hook -> copy the URL it gives you.
REM
REM   Put that URL in a file named deploy-hook.txt in this folder (automation\),
REM   one line, nothing else. It is deliberately not stored here, in the task,
REM   or in the repo — .gitignore excludes it, same reasoning the original
REM   board's VAULT_CREDENTIALS had: a scheduled job or a committed file that
REM   can redeploy your site is a scheduled job or a committed file that leaks
REM   the ability to redeploy your site if this machine or repo is ever
REM   shared. Without it, this script pushes the data and tells you to click
REM   "Manual Deploy" on Render yourself — which is exactly as safe, just not
REM   automatic.

setlocal enabledelayedexpansion
cd /d "%~dp0\.."

set "YES="
if /i "%~1"=="-y"     ( set "YES=1" & shift )
if /i "%~1"=="--yes"  ( set "YES=1" & shift )

set "MSG=%~1"
if not defined MSG set "MSG=Update candidates queue as of %date%"

REM ---------------------------------------------------------------- sanity
if not exist "backend\data\candidates_raw.json" (
  echo ERROR: backend\data\candidates_raw.json is missing. Run update-weekly.mjs first.
  exit /b 3
)
REM candidates_raw.json is written from automation\data\candidates-queue.json's
REM open subset — if the queue has moved on since the last publish this catches
REM it, the same class of bug the original board's staleness guard existed for
REM (data updated, live site unchanged, no error anywhere).
powershell -NoProfile -ExecutionPolicy Bypass -Command "exit $(if((Get-Item 'automation\data\candidates-queue.json' -ErrorAction SilentlyContinue).LastWriteTimeUtc -gt (Get-Item 'backend\data\candidates_raw.json').LastWriteTimeUtc){1}else{0})"
if errorlevel 1 (
  echo.
  echo The queue has changed since candidates_raw.json was last written.
  echo Run this first, from automation\:   node update-weekly.mjs --stamp-only
  echo Then run this script again.
  exit /b 1
)

REM ---------------------------------------------------------------------- staging
REM Only these two derived files, plus this pipeline's own record. Never the
REM 375-company dataset — that stays a decision you make with the same
REM research depth the existing companies got, not something this script
REM should ever touch.
git add backend\data\candidates_raw.json backend\data\build_stamp.json
git add automation\data\candidates-queue.json automation\data\reviewed-symbols.json
if exist "automation\data\profiles" git add automation\data\profiles
if exist "automation\data\weekly-report.md" git add automation\data\weekly-report.md

git diff --cached --quiet
if not errorlevel 1 (
  echo Nothing staged - the working tree matches the last commit. Nothing to publish.
  exit /b 0
)

echo.
echo About to commit and push:
echo.
git diff --cached --stat
echo.

if defined YES goto :confirmed
set /p "OK=Push to origin main and redeploy the site? [y/N] "
if /i not "%OK%"=="y" (
  echo Aborted. Nothing was committed; the staging area is left as it is.
  exit /b 130
)
:confirmed

git commit -m "%MSG%"
if errorlevel 1 (
  echo ERROR: commit failed - see above. Nothing pushed.
  exit /b 1
)

git push
if errorlevel 1 (
  echo.
  echo ERROR: push failed - see above. The commit exists locally but the site is unchanged.
  echo        Fix the cause and run:  git push
  exit /b 1
)

echo.
echo Pushed.

REM ------------------------------------------------------------ trigger deploy
if not exist "automation\deploy-hook.txt" (
  echo.
  echo No automation\deploy-hook.txt found, so the site has NOT been redeployed yet.
  echo Pushed data with no matching deploy looks exactly like a stale site - go to
  echo Render -^> deep-microcap-screener -^> Manual Deploy -^> Deploy latest commit,
  echo or create a Deploy Hook (see the comment at the top of this file) so this
  echo step can do it for you next time.
  exit /b 0
)

set /p "HOOK=" < "automation\deploy-hook.txt"
if not defined HOOK (
  echo automation\deploy-hook.txt is empty. Nothing to trigger. Deploy manually on Render.
  exit /b 0
)

echo Triggering Render deploy...
curl -s -o nul -w "Render responded: %%{http_code}\n" -X POST "%HOOK%"
echo Render usually finishes a Docker build within a few minutes.
echo Check it at your Render URL - log in and open the Candidates queue to confirm
echo it changed, because a stale build looks exactly like a fresh one.
exit /b 0
