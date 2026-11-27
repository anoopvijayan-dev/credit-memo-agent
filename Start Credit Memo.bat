@echo off
rem Starts the credit memo case app and opens it in your browser.
rem Keep this window open while you work; close it to stop the app.
cd /d "%~dp0"
if exist ".venv\Scripts\activate.bat" (
  call ".venv\Scripts\activate.bat"
) else if exist "venv\Scripts\activate.bat" (
  call "venv\Scripts\activate.bat"
)
where cma >nul 2>nul
if errorlevel 1 (
  echo The credit memo tool is not installed in this folder.
  echo Ask your administrator to install it, then try again.
  pause
  exit /b 1
)
cma app
pause
