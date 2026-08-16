@echo off
REM =====================================================================
REM  Nasb-e khodkar-e pishniaz-ha-ye proje-ye irica (Windows)
REM  Ejra: rooye file dabl-click konid ya dar CMD/PowerShell -> install.bat
REM =====================================================================

setlocal
cd /d "%~dp0"

echo.
echo === Check kardan-e Python ===
where python >nul 2>&1
if errorlevel 1 (
    echo [KHATA] Python roo-ye system nasb nist ya dar PATH nist.
    echo Lotfan Python 3.9+ ra az https://www.python.org/downloads/ nasb konid
    echo va "Add Python to PATH" ra tik bezanid.
    pause
    exit /b 1
)

python --version

echo.
echo === Update kardan-e pip ===
python -m pip install --upgrade pip

echo.
echo === Nasb-e ketabkhane-ha az requirements.txt ===
python -m pip install -r requirements.txt
if errorlevel 1 (
    echo.
    echo [KHATA] Nasb-e ketabkhane-ha namovaffagh bood.
    pause
    exit /b 1
)

echo.
echo === Tamam ===
echo Hame-ye pishniaz-ha nasb shodand.
echo Baraye ejra:
echo    python captcha_collector.py --site irica
echo    python labeler\label_server.py
echo.
pause
endlocal
