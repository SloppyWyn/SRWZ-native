@echo off
rem Builds PS2Recomp's tools (third_party\PS2Recomp) with MSVC: the recompiler, the
rem analyzer and the tests; not the optional GUI studio, not the game runner
rem (tools\build-game.bat).
rem     tools\build-ps2recomp.bat [Release|Debug]
rem From a script or agent start it through tools\launch.ps1 (no console window; everything
rem it runs shares that windowless console):
rem     powershell -NoProfile -ExecutionPolicy Bypass -File tools\launch.ps1 -- tools\build-ps2recomp.bat
setlocal
set CONFIG=%1
if "%CONFIG%"=="" set CONFIG=Release
call "%~dp0configure-ps2recomp.bat" || exit /b 1
"C:\Program Files\CMake\bin\cmake.exe" --build "%OUT%" --config %CONFIG% --parallel ^
    --target ps2_recomp ps2_analyzer ps2x_tests || exit /b 1
