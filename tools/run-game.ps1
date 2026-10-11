# Runs the recompiled game for a while, then stops it gracefully (stop file + window close, see
# tools\runstop.ps1, so stdout flushes) and keeps the logs in local\runs\<name>.out.txt / .err.txt.
#     powershell -File tools\run-game.ps1 -Name run4 -Seconds 40 -Trace "Sif,Cd,Open,Read,Lseek" [-Headless] [-Sound]
# No audio device by default (PS2X_NO_AUDIO=1, tools\runstop.ps1 Set-RunnerAudio); -Sound plays sound.
# -Trace sets PS2X_TRACE_STUBS (1 = every stub call; a comma list = names containing one).
# -Headless sets PS2X_HEADLESS=1 (no window); the runner also goes headless by itself when there
# is no monitor. -Exe <path> runs another runner build (default: paths.runner of the game config).
param(
    [string]$Name = "run",
    [int]$Seconds = 40,
    [string]$Trace = "",
    [switch]$Headless,
    [switch]$Sound,
    [string]$Exe = ""
)
# Paths come from config\game.json (main checkout's local\ also from a worktree).
. (Join-Path $PSScriptRoot "gameconfig.ps1")
. (Join-Path $PSScriptRoot "runstop.ps1")
$cfg = Get-GameConfig
$root = $cfg._root
$exe = if ($Exe) { (Resolve-Path $Exe).Path } else { Resolve-GamePath $cfg 'runner' }
$runs = Resolve-GamePath $cfg 'runs'
$elf = Resolve-GamePath $cfg 'elf'
$disc = Resolve-GamePath $cfg 'disc'
$wd = Resolve-GamePath $cfg 'workdir'; if (-not $wd) { $wd = Split-Path -Parent $elf }
$runArgs = @($elf) + $(if ($disc) { @($disc) } else { @() })
New-Item -ItemType Directory -Force $runs | Out-Null
$out = Join-Path $runs "$Name.out.txt"
$err = Join-Path $runs "$Name.err.txt"
# Silent, no audio device, unless -Sound or the caller set PS2X_MUTE=0 (Wyn, 2026-10-06).
Set-RunnerAudio ([bool]$Sound)
if ($Trace) { $env:PS2X_TRACE_STUBS = $Trace } else { Remove-Item Env:PS2X_TRACE_STUBS -ErrorAction SilentlyContinue }
if ($Headless) { Set-RunnerHeadless }
$stop = New-RunnerStopFile (Join-Path $runs $Name)
# tools\launch.ps1 (via runstop.ps1): no console window; the game window, if any, opens behind
# Wyn's window (GLFW activates it on show: the focus guard hands the foreground back).
$p = Start-Quiet $exe -ArgumentList $runArgs -WorkingDirectory $wd -StdOut $out -StdErr $err -FocusGuardSeconds $(if ($Headless) { 0 } else { 60 })
if ($p.WaitForExit($Seconds * 1000)) {
    "exited by itself, code $($p.ExitCode)"
    Remove-Item -LiteralPath $stop -Force -ErrorAction SilentlyContinue
} else {
    $how = Stop-Runner $p $stop
    "$how after $Seconds s$(if (Test-RunnerHeadless $out) { ' (headless)' })"
}
"stdout: $((Get-Content $out).Count) lines, stderr: $((Get-Content $err).Count) lines -> $runs\$Name.*.txt"
