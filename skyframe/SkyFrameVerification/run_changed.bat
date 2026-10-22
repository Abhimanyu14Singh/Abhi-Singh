@echo off
rem SkyFrame ETABS verification: run the new or changed models in ETABS 23, then push the results to GitHub.
rem Place this file in C:\SkyFrameVerification (next to run_etabs.py) and double-click it.
setlocal
set "REPO=C:\Users\abhimanyu.singh\Documents\GitHub\Abhi-Singh"
set "OUT=%REPO%\skyframe\SkyFrameVerification"
set "RAW=https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/claude/etabs-opensees-ui-rebuild-kt2zwh/skyframe/verification/etabs/run_etabs.py"

cd /d "%~dp0"
if not exist run_etabs.py curl -L -o run_etabs.py "%RAW%"
python run_etabs.py --update
python run_etabs.py --all --out "%OUT%"

echo.
echo Pushing the results to GitHub ...
cd /d "%REPO%" || (echo Could not find %REPO% & pause & exit /b 1)
git pull --no-edit
git add skyframe/SkyFrameVerification
git commit -m "ETABS 23 verification results"
git push
if errorlevel 1 (echo. & echo The push did not go through: copy the messages above and send them to Claude.) else (echo. & echo Done. The results are on GitHub.)
pause
