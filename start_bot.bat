@echo off
chcp 65001 >nul
title Kon German Bot - Telegram AI Teacher 🤓
cd /d "%~dp0"
:loop
echo ======================================================
echo 🤓 Dang khoi dong Kon German Bot (@kon_german_bot)...
echo ======================================================
python -u bot.py
echo ⚠️ Bot vua tam ngung, tu dong khoi dong lai sau 3 giay...
timeout /t 3 >nul
goto loop
