@echo off
rem SkyFrame ETABS verification: run the real projects (case R1) in ETABS 23, then push the results to GitHub.
rem Put the ETABS models (.EDB) in %OUT%\RealProjects first.  Place this file in C:\SkyFrameVerification
rem (next to run_etabs.py) and double-click it.  run_changed.bat runs R1 only the first time; this reruns it.
setlocal
set "REPO=C:\Users\abhimanyu.singh\Documents\GitHub\Abhi-Singh"
set "OUT=%REPO%\skyframe\SkyFrameVerification"
set "RAW=https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/claude/etabs-opensees-ui-rebuild-kt2zwh/skyframe/verification/etabs/run_etabs.py"

cd /d "%~dp0"
rem the latest runner; the random query gets past GitHub's few-minute cache of the file
curl -f -s -L -o run_etabs.new "%RAW%?t=%RANDOM%%RANDOM%" && move /y run_etabs.new run_etabs.py >nul
python run_etabs.py --update
if not exist "%OUT%\RealProjects" mkdir "%OUT%\RealProjects"
python run_etabs.py R1 --close --out "%OUT%"

echo.
echo Pushing the results to GitHub ...
cd /d "%REPO%" || (echo Could not find %REPO% & pause & exit /b 1)
git pull --no-edit
git add skyframe/SkyFrameVerification
git commit -m "ETABS 23 verification results: real projects (R1)"
git push
if errorlevel 1 (echo. & echo The push did not go through: copy the messages above and send them to Claude.) else (echo. & echo Done. The results are on GitHub.)
pause
