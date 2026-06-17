@echo off
echo ============================================
echo   ETABS Dashboard - Build EXE
echo ============================================
echo.
echo Step 1: Installing PyInstaller...
pip install "pyinstaller>=6.0"
echo.
echo Step 2: Building ETABS_Dashboard.exe...
cd /d "%~dp0"
pyinstaller etabs_dashboard.spec --clean -y
echo.
if exist "dist\ETABS_Dashboard.exe" (
    echo SUCCESS: dist\ETABS_Dashboard.exe is ready.
) else (
    echo ERROR: Build failed. Check output above for details.
)
echo.
pause
