@echo off
chcp 65001 >nul
title Cai dat Kon German Bot tren PC moi
cd /d "%~dp0"
echo ======================================================
echo [1/3] Dang cai dat cac thu vien Python can thiet...
echo ======================================================
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
echo.
echo ======================================================
echo [2/3] Dang tao bieu tuong Kon German Bot ngoai Desktop...
echo ======================================================
powershell -NoProfile -Command " = New-Object -ComObject WScript.Shell; $s = $ws.CreateShortcut([Environment]::GetFolderPath('Desktop') + '\Kon German Bot.lnk'); $s.TargetPath = (Get-Item '.\start_bot.bat').FullName; $s.WorkingDirectory = (Get-Location).Path; $s.IconLocation = (Get-Item '.\kon_icon.ico').FullName; $s.Save()"
echo.
echo ======================================================
echo [3/3] HOAN TAT CAI DAT!
echo Hay dam bao ban da co file .env chua API Key & Token,
echo sau do nhap dup vao bieu tuong 'Kon German Bot' ngoai Desktop!
echo ======================================================
pause
