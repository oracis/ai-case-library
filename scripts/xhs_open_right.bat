@echo off
REM ==========================================================================
REM  Open Xiaohongshu creator center with the profile that HAS the drafts.
REM
REM  WHY THIS FILE EXISTS (2026-10-02)
REM  ------------------------------------
REMTwo profiles, SAME account, DIFFERENT drafts:
REM    C:\Users\DELL\chrome-debug-profile  -> 36 drafts  <- the real one
REM    %LOCALAPPDATA%\Google\ChromeCDP     -> 0 drafts   <- empty
REM  Drafts are LOCAL data bound to the profile, not server data bound to
REM  the account. Same account in two profiles = two separate draft boxes.
REM  The wechat pipeline uses ChromeCDP, so "reuse port 9222" would land
REM  you in the EMPTY one. Hence a dedicated port (9223) + profile.
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

if not exist "%XHS_PROFILE%" (
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
  echo [i] port %PORT% already up - opening draft box in that window.
  start "" "%DRAFT%"
) else (
  REM stale LOCK makes Chrome exit instantly with no error
  if exist "%XHS_PROFILE%\Default\LOCK" del /f /q "%XHS_PROFILE%\Default\LOCK" >nul 2>&1
  start "" "%CFT%" --remote-debugging-port=%PORT% --user-data-dir="%XHS_PROFILE%" --no-first-run --no-default-browser-check --no-sandbox "%DRAFT%"
  ping -n 8 127.0.0.1 >nul
)

echo.
echo [OK] profile : %XHS_PROFILE%
echo [OK] port   : %PORT%   (wechat uses 9222 - do NOT mix them)
echo [OK] url    : %DRAFT%
echo.
echo     EXPECT sidebar draft count: 36
echo     If it shows 0, the empty ChromeCDP profile is in front - close it.
echo.
pause
endlocal