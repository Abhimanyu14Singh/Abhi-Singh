@echo off
rem SkyFrame speed benchmarks, SkyFrame side only: times the latest SkyFrame again on SP1 to SP5 against the
rem ETABS runs already stored here (ETABS is not opened), then pushes to GitHub. About 10-15 minutes.
rem Place this file in C:\SkyFrameVerification (next to run_etabs.py) and double-click it.
setlocal
set "REPO=C:\Users\abhimanyu.singh\Documents\GitHub\Abhi-Singh"
set "OUT=%REPO%\skyframe\SkyFrameVerification"
set "RAW=https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/claude/etabs-opensees-ui-rebuild-kt2zwh/skyframe/verification/etabs/run_etabs.py"

cd /d "%~dp0"
rem the latest runner; the random query gets past GitHub's few-minute cache of the file
curl -f -s -L -o run_etabs.new "%RAW%?t=%RANDOM%%RANDOM%" && move /y run_etabs.new run_etabs.py >nul
python run_etabs.py --update
python run_etabs.py SP1 SP2 SP3 SP4 SP5 --skyframe-only --out "%OUT%"

echo.
echo Pushing the results to GitHub ...
cd /d "%REPO%" || (echo Could not find %REPO% & pause & exit /b 1)
git pull --no-edit
if not exist skyframe\SkyFrameVerification\.gitignore (echo The results filter .gitignore is missing: run fix_push.bat & pause & exit /b 1)
git config http.postBuffer 524288000
git add skyframe/SkyFrameVerification
git commit -m "SkyFrame speed timings SP1-SP5 (latest SkyFrame)"
git push
if errorlevel 1 (echo. & echo The push did not go through: copy the messages above and send them to Claude.) else (echo. & echo Done. The results are on GitHub.)
pause
