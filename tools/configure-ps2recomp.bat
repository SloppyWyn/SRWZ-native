@echo off
rem Shared by build-ps2recomp.bat and build-game.bat: sets up MSVC and configures
rem third_party\PS2Recomp into out\build. Leaves OUT set for the caller.
call "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat" >nul 2>&1 || exit /b 1
set ROOT=%~dp0..
set SRC=%ROOT%\third_party\PS2Recomp
set OUT=%SRC%\out\build
rem PS2X_GAME_DIR: the runner compiles the recompiled game from local\ (never from the submodule).
rem CMAKE_POLICY_VERSION_MINIMUM: CMake 4 refuses fetched libraries that still ask for CMake < 3.5.
"C:\Program Files\CMake\bin\cmake.exe" -S "%SRC%" -B "%OUT%" -G "Visual Studio 17 2022" -A x64 ^
    -DPS2X_BUILD_STUDIO=OFF -DCMAKE_POLICY_VERSION_MINIMUM=3.5 ^
    -DPS2X_GAME_DIR="%ROOT%\local\recomp\output" ^
    -DPS2X_OVERLAY_DIR="%ROOT%\local\recomp\overlay_build" || exit /b 1
