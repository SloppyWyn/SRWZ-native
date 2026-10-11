@echo off
rem Builds the game runner (ps2EntryRunner.exe) from the recompiled code in
rem local\recomp\output (run ps2_recomp on local\recomp\srwz.toml first).
rem     tools\build-game.bat [Release|Debug]
rem Holds the "build" lock (tools\lock.ps1) for the whole build: one runner build at a time
rem across every session and worktree, since a full build needs most of the RAM.
rem From a script or agent start it through tools\launch.ps1 (no console window; everything
rem it runs shares that windowless console):
rem     powershell -NoProfile -ExecutionPolicy Bypass -File tools\launch.ps1 -- tools\build-game.bat
setlocal
set CONFIG=%1
if "%CONFIG%"=="" set CONFIG=Release
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lock.ps1" -Acquire -Name build -Owner build-game -HolderParent || exit /b 1
call "%~dp0configure-ps2recomp.bat" || goto :fail
"C:\Program Files\CMake\bin\cmake.exe" --build "%OUT%" --config %CONFIG% --parallel ^
    --target ps2EntryRunner || goto :fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lock.ps1" -Release -Name build -HolderParent >nul
exit /b 0
:fail
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0lock.ps1" -Release -Name build -HolderParent >nul
exit /b 1
