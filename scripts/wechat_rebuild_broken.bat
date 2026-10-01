@echo off
REM ============================================================
REM  Rebuild the 6 corrupted WeChat drafts (320003 record damage)
REM
REM  NOTE: This file MUST stay pure ASCII. cmd.exe reads .bat using
REM  the OEM codepage (GBK on this machine); any Chinese byte in a
REM  REM/comment/echo line corrupts parsing and silently drops the
REM  following commands (that is how "cd /d" got lost and the path
REM  became scripts\scripts\...).
REM
REM  Usage:
REM     scripts\wechat_rebuild_broken.bat          dry run (show plan)
REM     scripts\wechat_rebuild_broken.bat --go     actually publish
REM
REM  Run on the LOCAL machine (double-click).
REM ============================================================
setlocal EnableExtensions

set "SCRIPT_DIR=%~dp0"
set "ROOT=%~dp0.."
set "PROF=%LOCALAPPDATA%\Google\ChromeCDP"
set "CFT=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe"

REM The proxy MUST be cleared, otherwise the CDP WebSocket gets cut
REM (10053 / 10054).
set HTTP_PROXY=
set HTTPS_PROXY=
set http_proxy=
set https_proxy=
set ALL_PROXY=
set all_proxy=
set PYTHONIOENCODING=utf-8

set GO=
if /i "%~1"=="--go" set GO=--go

REM ---- resolve a python interpreter -------------------------------
set "PY="
where python >nul 2>nul && set "PY=python"
if not defined PY (
  if exist "%LOCALAPPDATA%\.workbuddy\binaries\python\versions\3.13.12\python.exe" (
    set "PY=%LOCALAPPDATA%\.workbuddy\binaries\python\versions\3.13.12\python.exe"
  )
)
if not defined PY (
  echo [X] No python interpreter found on PATH.
  pause
  exit /b 1
)

echo === 0) Preflight: clear stale LOCK ===
REM A leftover LOCK makes Chrome exit instantly (no error, no window).
if exist "%PROF%\Default\LOCK" (
  echo   found LOCK, deleting...
  del /f /q "%PROF%\Default\LOCK" 2>nul
) else (
  echo   no LOCK
)

echo.
echo === 1) Start Chrome (CDP) if not running ===
curl -s --noproxy * --max-time 4 http://127.0.0.1:9222/json/version 2>nul | findstr /c:"webSocketDebuggerUrl" >nul
if errorlevel 1 (
  echo   starting Chrome for Testing...
  if exist "%CFT%" (
    start "" "%CFT%" --remote-debugging-port=9222 --user-data-dir="%PROF%" --no-first-run --no-default-browser-check
  ) else (
    echo   [warn] Chrome for Testing not found, falling back to system Chrome
    start "" "%ProgramFiles%\Google\Chrome\Application\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%PROF%"
  )
  REM See chrome_cdp_check.bat: GNU "timeout" shadows the cmd builtin.
  ping -n 13 127.0.0.1 >nul
)

curl -s --noproxy * --max-time 5 http://127.0.0.1:9222/json/version 2>nul | findstr /c:"Browser" >nul
if errorlevel 1 (
  echo.
  echo [X] CDP port 9222 not reachable.
  echo.
  echo     Close ALL Chrome windows first (check Task Manager for chrome.exe),
  echo     then double-click this file again.
  echo.
  echo     Manual fallback:
  echo       "%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe" --remote-debugging-port=9222 --user-data-dir="%PROF%"
  echo       "%ProgramFiles%\Google\Chrome\Application\chrome.exe"            --remote-debugging-port=9222 --user-data-dir="%PROF%"
  echo.
  echo     After it starts, http://127.0.0.1:9222/json/version must return JSON.
  pause
  exit /b 1
)
echo   [OK] CDP port is up

echo.
echo === 2) Login probe (real token check, not cookie size) ===
"%PY%" -X utf8 "%SCRIPT_DIR%chrome_login_state.py"
echo.

echo.
echo === 3) Current state of the 6 damaged records ===
"%PY%" -X utf8 -u "%SCRIPT_DIR%wechat_publish.py" delete --appmsgid 100000125,100000130,100000134,100000138,100000142,100000146 --dry

echo.
echo === 4) Rebuild one by one ===
for %%C in (magicslides-app stan 1lookup gojiberryai sierra genius-ai) do (
  echo.
  echo -------- %%C --------
  "%PY%" -X utf8 -u "%SCRIPT_DIR%wechat_publish.py" publish --case %%C %GO%
  if errorlevel 1 echo [warn] %%C failed, see the log above
)

echo.
echo === 5) Remote read-back (by appmsgid, do not trust "saved OK") ===
"%PY%" -X utf8 "%SCRIPT_DIR%wechat_verify_refresh.py" --all

echo.
echo === Done ===
echo Manually confirm the 6 new drafts (title / body / cover).
echo Then:
echo   1) write the new appmsgid values back to data\wechat_published.json
echo   2) remove those 6 entries from data\wechat_broken_drafts.json
pause
endlocal