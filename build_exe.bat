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
echo [2/3] Building with PyInstaller in FOLDER mode (takes 1-3 minutes) ...
python -m PyInstaller --noconfirm --clean --windowed --name LOLCounterPicker --exclude-module cryptography --exclude-module PIL --exclude-module numpy run_app.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. Scroll up for details.
    pause
    exit /b 1
)

echo.
echo [3/3] Done.
echo ============================================================
echo   Output : dist\LOLCounterPicker\  (folder; run the exe inside)
echo.
echo   FOLDER mode keeps runtime files on disk instead of extracting
echo   to a temp dir, which avoids antivirus DLL-load errors.
echo   To distribute, zip the folder and upload the zip as a Release asset.
echo   SmartScreen/antivirus may warn once (unsigned) -
echo   choose "More info" -^> "Run anyway".
echo ============================================================
pause
