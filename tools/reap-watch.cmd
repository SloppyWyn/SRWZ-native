@echo off
rem Double-click: start tools\reap.ps1 -Watch hidden, below normal priority, no window.
rem It ends leftover processes of our builds and runs (see reap.ps1); log local\reap.log.
rem Stop it in Task Manager (powershell.exe running reap.ps1 -Watch).
set SRWZ_LAUNCH_PRIORITY=BelowNormal
powershell -NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "%~dp0launch.ps1" -NoWait -- powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0reap.ps1" -Watch -Quiet
