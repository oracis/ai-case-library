@echo off
rem ModelScope manual login entry for daily Magicube check-in.
rem Uses an ISOLATED profile (chrome-ms-profile) because Chrome 136+ refuses
rem to open a debugging port on the default user-data-dir, and App-Bound
rem encryption makes copied cookies undecryptable.
rem ASCII only - do NOT add non-ASCII text (GBK corruption on Windows).
start "" "%LOCALAPPDATA%\Google\ChromeForTesting\chrome-win64\chrome.exe" --user-data-dir="C:\Users\DELL\chrome-ms-profile" https://www.modelscope.cn/magicube/usage
