@echo off
rem SkyFrame speed benchmarks, linear: SP4 (large 10-story building) and SP5 (70-story shear-wall tower),
rem timed in ETABS 23 and SkyFrame on this PC, then pushed to GitHub. About 30-45 minutes.
rem Place this file in C:\SkyFrameVerification (next to run_etabs.py) and double-click it.
setlocal
set "REPO=C:\Users\abhimanyu.singh\Documents\GitHub\Abhi-Singh"
set "OUT=%REPO%\skyframe\SkyFrameVerification"
set "RAW=https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/claude/etabs-opensees-ui-rebuild-kt2zwh/skyframe/verification/etabs/run_etabs.py"

cd /d "%~dp0"
rem the latest runner; the random query gets past GitHub's few-minute cache of the file
curl -f -s -L -o run_etabs.new "%RAW%?t=%RANDOM%%RANDOM%" && move /y run_etabs.new run_etabs.py >nul
python run_etabs.py --update
python run_etabs.py SP4 SP5 --close --out "%OUT%"

echo.
echo Pushing the results to GitHub ...
cd /d "%REPO%" || (echo Could not find %REPO% & pause & exit /b 1)
git pull --no-edit
rem ETABS's analysis files are kept out by skyframe/SkyFrameVerification/.gitignore (they are gigabytes)
if not exist skyframe\SkyFrameVerification\.gitignore (echo The results filter .gitignore is missing: run fix_push.bat & pause & exit /b 1)
git config http.postBuffer 524288000
git add skyframe/SkyFrameVerification
git commit -m "ETABS 23 speed benchmarks SP4, SP5"
git push
if errorlevel 1 (echo. & echo The push did not go through: copy the messages above and send them to Claude.) else (echo. & echo Done. The results are on GitHub.)
pause
