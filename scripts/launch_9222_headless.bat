@echo off
REM Launch a DETACHED HEADLESS Chrome for WeChat MP (port 9222, profile ChromeCDP).
REM
REM Why headless + detached:
REM   1) HEADLESS avoids Windows foreground-window throttling. A headed Chrome
REM      sitting in the background gets its rendering/timers throttled, which
REM      stalls CDP eval (observed: _open_draft_editor hung >60s).
REM   2) DETACHED (start) keeps Chrome alive independent of the agent sandbox,
REM      so refresh tasks only CONNECT to it and survive task reclamation.
REM
REM Double-click this file (NOT via WorkBuddy). No window will appear -- that is
REM expected for headless. Leave it running while the refresh tasks run.
setlocal
set "CHROME=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe"
set "PROFILE=%LOCALAPPDATA%\Google\ChromeCDP"
if not exist "%CHROME%" set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
echo Starting HEADLESS Chrome on port 9222 (profile ChromeCDP)...
start "WeChat-MP-Chrome-HL" "%CHROME%" --headless=new --disable-gpu --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --no-sandbox --remote-allow-origins=* about:blank
echo Done. Chrome is running headless (no visible window is normal).
echo Tell the agent to continue.
pause
