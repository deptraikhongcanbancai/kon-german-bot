@echo off
chcp 65001 >nul
set PYTHONIOENCODING=utf-8
title Kon German Bot - Telegram AI Teacher
cd /d "%~dp0"
:loop
echo ======================================================
echo [KON BOT] Dang khoi dong Kon German Bot (@kon_german_bot)...
echo ======================================================
"C:\Users\khoa0\AppData\Local\Programs\Python\Python312\python.exe" -u bot.py
echo [KON BOT] Bot vua tam ngung, tu dong khoi dong lai sau 3 giay...
timeout /t 3 >nul
goto loop
