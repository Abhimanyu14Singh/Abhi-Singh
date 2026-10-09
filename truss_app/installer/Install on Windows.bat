@echo off
rem Double-click launcher for the Truss Lab Windows installer.
rem It just runs install-windows.ps1 (sitting next to this file).
title Truss Lab installer
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install-windows.ps1"
echo.
pause
