@echo off
cd /d "%~dp0%"
echo ============================================================
echo   Build LOL Counter Picker - standalone EXE (no Python needed)
echo ============================================================
echo.

where python >nul 2>nul
if errorlevel 1 (
    echo [ERROR] Python not found on THIS machine.
    echo Install Python 3.9+ from https://www.python.org/downloads/
    echo and tick "Add Python to PATH" during install.
    pause
    exit /b 1
)

echo [1/3] Installing build dependencies (requests, pyinstaller) ...
python -m pip install --upgrade pip requests urllib3 pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed - check your network, then retry.
    pause
    exit /b 1
)

echo.
echo [2/3] Building EXE with PyInstaller (takes 1-3 minutes) ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name LOLCounterPicker run_app.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. Scroll up for details.
    pause
    exit /b 1
)

echo.
echo [3/3] Done.
echo ============================================================
echo   EXE file : dist\LOLCounterPicker.exe        (test it first)
echo.
echo   No local zip is created. To distribute, upload the exe as
echo   an asset of the corresponding GitHub Release.
echo   SmartScreen/antivirus may warn once (unsigned exe) -
echo   choose "More info" -^> "Run anyway".
echo ============================================================
pause
