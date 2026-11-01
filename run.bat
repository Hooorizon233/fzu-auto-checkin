@echo off
rem FZU attendance auto check-in - used by Windows Task Scheduler.
rem Run manually by double-clicking, or: run.bat
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
cd /d "%~dp0"
py -3.12 main.py >> "%~dp0checkin.log" 2>&1
