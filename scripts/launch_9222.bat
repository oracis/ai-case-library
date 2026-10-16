@echo off
REM Launch a DETACHED headed Chrome for WeChat MP (port 9222, profile ChromeCDP).
REM Double-click this file (NOT via WorkBuddy) so Chrome belongs to YOUR session
REM and is NOT reclaimed by the agent sandbox. Keep the Chrome window open while
REM the refresh tasks run. Refresh only CONNECTS to it, never kills it.
setlocal
set "CHROME=%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe"
set "PROFILE=%LOCALAPPDATA%\Google\ChromeCDP"
if not exist "%CHROME%" set "CHROME=C:\Program Files\Google\Chrome\Application\chrome.exe"
echo Starting Chrome on port 9222 (profile ChromeCDP)...
start "WeChat-MP-Chrome" "%CHROME%" --remote-debugging-port=9222 --user-data-dir="%PROFILE%" --no-first-run --no-default-browser-check --no-sandbox --remote-allow-origins=* about:blank
echo Done. Leave the Chrome window open, then tell the agent to continue.
pause
