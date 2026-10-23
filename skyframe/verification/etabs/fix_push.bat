@echo off
rem One-time fix after the 1.5 GB push failed: keep this run's results, drop ETABS's own analysis
rem files from git (they stay on disk), and push only what the comparison reads.
setlocal
set "REPO=C:\Users\abhimanyu.singh\Documents\GitHub\Abhi-Singh"
set "BRANCH=claude/etabs-opensees-ui-rebuild-kt2zwh"
set "RAW=https://raw.githubusercontent.com/Abhimanyu14Singh/Abhi-Singh/%BRANCH%/skyframe/SkyFrameVerification/.gitignore"
cd /d "%REPO%" || (echo Could not find %REPO% & pause & exit /b 1)

rem undo the local results commit that never reached GitHub (all files stay on disk)
git reset -q origin/%BRANCH%
rem the filter that lets only results, logs, pictures and scripts through
curl -f -s -L -o skyframe\SkyFrameVerification\.gitignore "%RAW%?t=%RANDOM%%RANDOM%" || (echo Could not download the filter & pause & exit /b 1)
git rm -r -q --cached skyframe/SkyFrameVerification
git add skyframe/SkyFrameVerification
git commit -q -m "ETABS 23 verification results"
git pull --no-edit origin %BRANCH%
git config http.postBuffer 524288000
git push origin HEAD:%BRANCH%
if errorlevel 1 (echo. & echo The push did not go through: copy the messages above and send them to Claude.) else (echo. & echo Done. The results are on GitHub.)
pause
