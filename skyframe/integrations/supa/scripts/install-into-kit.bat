@echo off
rem Install the SkyFrame drop-in into a COPY of supa-fem-kit.
rem Usage: scripts\install-into-kit.bat C:\path\to\supa-fem-kit-copy
setlocal
set "KIT=%~1"
set "HERE=%~dp0.."
if "%KIT%"=="" (echo usage: %~nx0 C:\path\to\supa-fem-kit-copy & exit /b 2)
if not exist "%KIT%\src\types.ts" (echo %KIT% does not look like supa-fem-kit ^(no src\types.ts^) & exit /b 2)
if exist "%KIT%\src\builtin.ts" (echo already installed ^(%KIT%\src\builtin.ts exists^) & exit /b 2)
move "%KIT%\src\solve.ts" "%KIT%\src\builtin.ts" >nul || exit /b 1
copy /y "%HERE%\src\solve.ts" "%KIT%\src\solve.ts" >nul || exit /b 1
copy /y "%HERE%\test\xhr-sync.mjs" "%KIT%\xhr-sync.mjs" >nul || exit /b 1
echo Installed: src\solve.ts -^> src\builtin.ts (unchanged), new src\solve.ts, xhr-sync.mjs
echo Next, in %KIT%: npm install ^&^& npm run typecheck
