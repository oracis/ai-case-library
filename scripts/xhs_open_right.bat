@echo off
REM ==========================================================================
REM  Open Xiaohongshu creator center with the profile that HAS the drafts.
REM
REM  WHY THIS FILE EXISTS (2026-10-02)
REM  ------------------------------------
REM  Two profiles, SAME account, DIFFERENT drafts:
REM    C:\Users\DELL\chrome-debug-profile  -> the real one (48 @2026-10-17)
REM    %LOCALAPPDATA%\Google\ChromeCDP     -> 0 drafts   <- empty
REM  Drafts are LOCAL data bound to the profile (IndexedDB), not server
REM  data bound to the account. Same account in two profiles = two
REM  separate draft boxes. The wechat pipeline uses ChromeCDP, so
REM  "reuse port 9222" would land you in the EMPTY one. Hence 9223.
REM
REM  BUG FIXED 2026-10-17 (the "drafts vanished" report)
REM  ---------------------------------------------------
REM  The "port already up" branch used to run:
REM      start "" "%DRAFT%"
REM  which hands the URL to the DEFAULT browser = your DAILY Chrome
REM  (draft box 0), while the console still claimed "opening draft box
REM  in that window". Symptom: the FIRST run after a fresh boot worked
REM  (port down -> real launch), every LATER run silently opened the
REM  empty profile - exactly the "used to work, now it is gone" report.
REM  Fix: re-invoke Chrome for Testing with the SAME --user-data-dir.
REM  Chrome is a singleton per user-data-dir, so the running instance
REM  receives the URL ("Opening in existing browser session") and the
REM  tab lands in the window that actually has the drafts.
REM
REM  ASCII-ONLY ON PURPOSE: cmd.exe reads .bat as OEM codepage (GBK here).
REM  UTF-8 Chinese comments get truncated -> cmd eats the next line.
REM  Chinese notes live in scripts\xhs_open_right.md instead.
REM ==========================================================================
setlocal
set PORT=9223
set XHS_PROFILE=C:\Users\DELL\chrome-debug-profile
set CFT=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe
set DRAFT=https://creator.xiaohongshu.com/publish/publish?target=draft

if not exist "%XHS_PROFILE%\Default" (
  echo [X] profile not found: %XHS_PROFILE%
  pause
  exit /b 1
)
if not exist "%CFT%" (
  echo [X] Chrome for Testing not found: %CFT%
  pause
  exit /b 1
)

REM Is our dedicated port already serving that profile? Then just open a tab in it.
for /f "tokens=*" %%a in ('powershell -NoProfile -Command "try{(New-Object Net.Sockets.TcpClient).Connect('127.0.0.1',%PORT%);'UP'}catch{'DOWN'}" 2^>nul') do set ST=%%a

if "%ST%"=="UP" (
  echo [i] port %PORT% already up - handing the URL to THAT Chrome ^(same profile^).
  REM DO NOT "start "" %DRAFT%" here - that goes to the default browser
  REM ^(daily Chrome, draft box 0^). Same user-data-dir = same singleton
  REM instance = the tab lands in the window with the drafts.
  start "" "%CFT%" --user-data-dir="%XHS_PROFILE%" --no-first-run --no-default-browser-check --no-sandbox "%DRAFT%"
) else (
  REM stale LOCK makes Chrome exit instantly with no error
  if exist "%XHS_PROFILE%\Default\LOCK" del /f /q "%XHS_PROFILE%\Default\LOCK" >nul 2>&1
  start "" "%CFT%" --remote-debugging-port=%PORT% --user-data-dir="%XHS_PROFILE%" --no-first-run --no-default-browser-check --no-sandbox "%DRAFT%"
  ping -n 8 127.0.0.1 >nul
)

REM --- fail-soft read-back: print the sidebar draft count from THAT Chrome ---
REM Gives proof in the console that the tab landed in the right window.
set PY=
where python >nul 2>nul && set PY=python
if "%PY%"=="" where py >nul 2>nul && set PY=py -3
if "%PY%"=="" if exist "C:\Python313\python.exe" set PY="C:\Python313\python.exe"
if not "%PY%"=="" (
  %PY% -X utf8 "%~dp0xhs_draftbox_verify.py" --port %PORT%
)

echo.
echo [OK] profile : %XHS_PROFILE%
echo [OK] port   : %PORT%   (wechat uses 9222 - do NOT mix them)
echo [OK] url    : %DRAFT%
echo.
echo     EXPECT sidebar draft count: 48 (2026-10-17, grows over time)
echo     If it shows 0: the tab opened in your DAILY Chrome. Drafts are
echo     local IndexedDB bound to chrome-debug-profile - daily Chrome
echo     will NEVER show them. Close that tab and run this bat again.
echo.
pause
endlocal
