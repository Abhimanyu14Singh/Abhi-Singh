@echo off
echo ============================================
echo   ETABS v23 Dashboard - Starting...
echo ============================================
echo.
echo Make sure ETABS is open with your model loaded and analysis run.
echo.
cd /d "%~dp0"
python app.py
pause
