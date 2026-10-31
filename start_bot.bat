@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Kon German Bot - Telegram AI Teacher
cd /d "%~dp0"
:loop
echo ======================================================
echo [KON BOT] Dang khoi dong Kon German Bot (@kon_german_bot)...
echo ======================================================
where python >nul 2>nul
if %errorlevel%==0 (
    python -u bot.py
) else (
    py -u bot.py
)
echo [KON BOT] Bot vua tam ngung, tu dong khoi dong lai sau 3 giay...
timeout /t 3 >nul
goto loop
