@echo off
chcp 65001 >nul
title LOL Counter Picker
cd /d "%~dp0%"

where python >nul 2>nul
if errorlevel 1 (
    echo [错误] 未检测到 Python，请先安装 Python 3.9+：
    echo        https://www.python.org/downloads/
    echo        安装时务必勾选 "Add Python to PATH"
    pause
    exit /b 1
)

python -c "import requests" 2>nul
if errorlevel 1 (
    echo [初始化] 首次运行，正在安装依赖...
    python -m pip install requests urllib3
)

python -c "import PIL" 2>nul
if errorlevel 1 (
    echo [初始化] 安装高清头像组件 Pillow（可选，失败不影响使用）...
    python -m pip install Pillow
)

python -m lolcp
if errorlevel 1 pause
