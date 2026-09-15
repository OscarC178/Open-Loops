@echo off
rem Open Loops - double-click me.
rem   Not installed yet?  -> runs the installer (setup.ps1), which puts Open Loops in %LOCALAPPDATA%\OpenLoops
rem                          and an icon on your Desktop.
rem   Already installed?  -> starts it (hidden, no console window) and opens the page in your browser.
rem   (If this file sits inside an installed copy - e.g. one made by OpenLoops-Setup.exe - it runs that copy.
rem    A git checkout is never an installed copy: developers use `npm run dev` on port 8766, see CONTRIBUTING.md.)
setlocal
set "HERE=%~dp0"
if exist "%HERE%openloops\app.py" if exist "%HERE%config.json" if not exist "%HERE%.git" (
    rem Same check as the branch below. No `python` fallback: Windows ships a Store stub python.exe (but no
    rem pythonw.exe), so on a machine with no Python `where python` succeeds and would open the Store instead.
    where pythonw >nul 2>&1
    if errorlevel 1 (
        echo Python was not found on this computer's PATH. Run setup.ps1 again, or install Python from python.org and tick "Add to PATH".
        pause
        exit /b 1
    )
    cd /d "%HERE%"
    start "" pythonw -m openloops.app
    exit /b 0
)
set "APP=%LOCALAPPDATA%\OpenLoops\openloops\app.py"
if exist "%APP%" (
    where pythonw >nul 2>&1
    if errorlevel 1 (
        echo Python was not found on this computer's PATH. Run setup.ps1 again, or install Python from python.org and tick "Add to PATH".
        pause
        exit /b 1
    )
    cd /d "%LOCALAPPDATA%\OpenLoops"
    start "" pythonw -m openloops.app
    exit /b 0
)
echo Installing Open Loops...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0setup.ps1"
if errorlevel 1 pause
