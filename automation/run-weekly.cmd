@echo off
REM run-weekly.cmd — what Task Scheduler runs once a week.
REM
REM Two halves. The node scripts do the mechanical part: scan NSE/BSE for new
REM listings, sweep the small/mid-cap universe for names not on the board yet,
REM pull any new listing's prospectus, stamp the board. Claude Code then does
REM the part that needs judgement and the Screener/Trendlyne MCP servers,
REM following weekly-prompt.md. A node script cannot call an MCP server, which
REM is why the second half exists at all.
REM
REM Registering the task is left to you rather than done for you, because it
REM changes a system setting. From an ordinary (non-admin) prompt, in this
REM folder:
REM
REM   schtasks /create /tn "Microcap screener weekly discovery" /tr "\"%~f0\"" /sc weekly /d SAT /st 17:00 /f
REM
REM   schtasks cannot express the power settings, and its defaults stop the
REM   task the moment the laptop moves to battery — mid-fetch, with the week
REM   silently lost. So follow the create with, in PowerShell:
REM
REM     Set-ScheduledTask -TaskName "Microcap screener weekly discovery" -Settings (
REM       New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries `
REM         -ExecutionTimeLimit (New-TimeSpan -Hours 6) -MultipleInstances IgnoreNew)
REM
REM   check it     schtasks /query /tn "Microcap screener weekly discovery" /v /fo list
REM   run it now   schtasks /run   /tn "Microcap screener weekly discovery"
REM   remove it    schtasks /delete /tn "Microcap screener weekly discovery" /f
REM
REM Nothing in this file has push or deploy credentials. Discovery writes into
REM automation/data/ and backend/data/candidates_raw.json + build_stamp.json on
REM your disk only; getting those onto the live site is publish-candidates.cmd,
REM which you run yourself and which asks before it pushes.

setlocal
cd /d "%~dp0"

REM Task Scheduler starts with a bare environment, so node may not be on PATH.
where node >nul 2>&1 || (
  echo [%date% %time%] node not found on PATH >> "data\weekly.log"
  exit /b 9
)

REM Nor is claude. It is installed as an npm global, so look there before
REM giving up, and say which one is missing rather than failing with a bare
REM exit code at 5 p.m. on a Saturday.
set "CLAUDE=claude"
where claude >nul 2>&1 || set "CLAUDE=%APPDATA%\npm\claude.cmd"
if not exist "%CLAUDE%" (
  if /i not "%CLAUDE%"=="claude" (
    echo [%date% %time%] claude CLI not found - looked on PATH and in %%APPDATA%%\npm >> "data\weekly.log"
    echo [%date% %time%] discovery will still run; the judgement pass will not >> "data\weekly.log"
    set "CLAUDE="
  )
)

if not exist "data" mkdir "data"
echo. >> "data\weekly.log"
echo ===== %date% %time% ===== >> "data\weekly.log"
node update-weekly.mjs >> "data\weekly.log" 2>&1
set RC=%ERRORLEVEL%
echo exit code %RC% >> "data\weekly.log"

REM A missed week should be visible in the log rather than silently swallowed.
if not "%RC%"=="0" echo [%date% %time%] weekly pass reported failures - see above >> "data\weekly.log"

REM The mechanical half is worth keeping even when judgement can't run, so a
REM missing CLI does not fail the whole task.
if not defined CLAUDE (
  echo [%date% %time%] skipping judgement pass - no claude CLI >> "data\weekly.log"
  exit /b %RC%
)

echo. >> "data\weekly.log"
echo ----- judgement pass %date% %time% ----- >> "data\weekly.log"

REM bypassPermissions is deliberate and is the reason this runs in its own
REM folder. Nobody is at the keyboard, so a permission prompt does not get
REM answered - it hangs until the task times out and the week is silently
REM lost. The blast radius is bounded by what weekly-prompt.md tells it to
REM do: it reads companies up on Screener/Trendlyne and writes verdicts into
REM the queue. It has no git credentials and no Render deploy hook, so it
REM cannot ship anything even if it wanted to.
call "%CLAUDE%" -p "Follow the instructions in weekly-prompt.md exactly. You are running unattended on a schedule." ^
  --permission-mode bypassPermissions ^
  --add-dir "%~dp0" >> "data\weekly.log" 2>&1
set CRC=%ERRORLEVEL%
echo claude exit code %CRC% >> "data\weekly.log"
if not "%CRC%"=="0" echo [%date% %time%] judgement pass reported failures - see above >> "data\weekly.log"

REM Belt and braces: re-stamp regardless of whether the judgement pass got to
REM its own final step, so backend/data/build_stamp.json and candidates_raw.json
REM always reflect whatever verdicts made it into the queue this run.
node update-weekly.mjs --stamp-only >> "data\weekly.log" 2>&1

echo [%date% %time%] done. Review the queue, then ship with: publish-candidates.cmd >> "data\weekly.log"

if not "%RC%"=="0" exit /b %RC%
exit /b %CRC%
