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

echo [1/4] Installing build dependencies (requests, pyinstaller) ...
python -m pip install --upgrade pip requests urllib3 pyinstaller
if errorlevel 1 (
    echo [ERROR] pip install failed - check your network, then retry.
    pause
    exit /b 1
)

echo.
echo [2/4] Building EXE with PyInstaller (takes 1-3 minutes) ...
python -m PyInstaller --noconfirm --clean --onefile --windowed --name LOLCounterPicker run_app.py
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed. Scroll up for details.
    pause
    exit /b 1
)

echo.
echo [3/4] Creating shareable zip ...
if exist "LOL-Counter-Picker-EXE.zip" del /q "LOL-Counter-Picker-EXE.zip"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\LOLCounterPicker.exe' -DestinationPath 'LOL-Counter-Picker-EXE.zip' -Force"
if errorlevel 1 (
    echo [WARN] zip creation failed, but the EXE itself is ready:
    echo        dist\LOLCounterPicker.exe
    pause
    exit /b 1
)

echo.
echo [4/4] Done.
echo ============================================================
echo   EXE file : dist\LOLCounterPicker.exe        (test it first)
echo   Share zip: LOL-Counter-Picker-EXE.zip       (send to friends)
echo.
echo   Friends only need to unzip and double-click the exe.
echo   SmartScreen/antivirus may warn once (unsigned exe) -
echo   choose "More info" -^> "Run anyway".
echo ============================================================
pause
