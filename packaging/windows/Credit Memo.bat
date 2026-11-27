@echo off
rem Credit Memo: starts the app on this computer and opens it in your browser.
rem Keep this window open while you work. Close it to stop Credit Memo.
setlocal
cd /d "%~dp0"
if not defined CMA_HOME set "CMA_HOME=%LOCALAPPDATA%\Credit Memo"
"%~dp0python\python.exe" -m cma.app.launcher %*
if errorlevel 1 pause
