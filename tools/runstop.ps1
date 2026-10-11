# Starting and stopping the runner cleanly, with or without a window (shared by run-game.ps1,
# long-run.ps1 and the scripts built on them). Dot-source it:  . "$PSScriptRoot\runstop.ps1"
#     $stop = New-RunnerStopFile <log base>   PS2X_STOP_FILE=<log base>.stop for the next runner
#     Set-RunnerHeadless                       PS2X_HEADLESS=1 for the next runner
#     Set-RunnerAudio [$sound]                 no audio device (default) or sound on (-Sound)
#     Stop-Runner $proc $stop [-WaitSeconds 10] -> "closed gracefully" | "force-killed" | "had exited"
#     powershell -File tools\runstop.ps1 -SelfTest
# The runner (PS2Recomp srwz-headless and later) stops cleanly once the stop file exists: guest
# stopped, stdout/stderr flushed, exit code 0. That is the only clean stop for a headless runner
# (PS2X_HEADLESS=1, or no monitor: it then says "[run] no monitor: headless"), which has no window
# to close. Stop-Runner also sends a window close, so older windowed runners stop as before;
# a forced kill (loses buffered stdout) is the last resort.
# No param block: dot-sourcing would overwrite the caller's own -SelfTest switch.
# Processes start through tools\launch.ps1 (Start-Quiet: no console window, no focus steal).
. (Join-Path $PSScriptRoot "launch.ps1")

function New-RunnerStopFile([string]$base) {
    $path = "$base.stop"
    Remove-Item -LiteralPath $path -Force -ErrorAction SilentlyContinue
    $env:PS2X_STOP_FILE = $path
    $path
}

function Set-RunnerHeadless { $env:PS2X_HEADLESS = "1" }

# Test runs stay silent (Wyn, 2026-10-06) and open no audio device at all: PS2X_NO_AUDIO=1
# (PS2Recomp srwz-noaudio and later: no InitAudioDevice, so no WASAPI stream opened and closed
# per run, which Wyn heard as his sound devices switching), plus PS2X_MUTE=1 for older
# runners, which open the device at zero volume. $sound (a script's -Sound), or a caller's
# PS2X_MUTE=0 / PS2X_NO_AUDIO=0, opens the device with sound on.
function Set-RunnerAudio([bool]$sound = $false) {
    if ($sound -or $env:PS2X_MUTE -eq '0' -or $env:PS2X_NO_AUDIO -eq '0') {
        $env:PS2X_NO_AUDIO = '0'; $env:PS2X_MUTE = '0'
        return
    }
    if (-not $env:PS2X_NO_AUDIO) { $env:PS2X_NO_AUDIO = '1' }
    if (-not $env:PS2X_MUTE) { $env:PS2X_MUTE = '1' }
}

# True when the runner's stdout says it runs without a window.
function Test-RunnerHeadless([string]$outPath) {
    if (-not (Test-Path -LiteralPath $outPath)) { return [bool]($env:PS2X_HEADLESS -and $env:PS2X_HEADLESS -ne '0') }
    $fs = [IO.File]::Open($outPath, 'Open', 'Read', 'ReadWrite')
    try { $text = (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Close() }
    return [bool]($text -match '\[run\] (headless|no monitor: headless)')
}

function Stop-Runner($proc, [string]$stopFile, [int]$WaitSeconds = 10) {
    if ($proc.HasExited) { $how = "had exited" }
    else {
        if ($stopFile) { Set-Content -LiteralPath $stopFile -Value "stop" -ErrorAction SilentlyContinue }
        try { $null = $proc.CloseMainWindow() } catch { }
        if ($proc.WaitForExit($WaitSeconds * 1000)) { $how = "closed gracefully" }
        else { Stop-Process -Id $proc.Id -Force -ErrorAction SilentlyContinue; $null = $proc.WaitForExit(5000); $how = "force-killed" }
    }
    if ($stopFile) { Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue }
    $how
}

if ($MyInvocation.InvocationName -ne '.' -and $args -contains '-SelfTest') {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("runstop-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $savedAudio = @($env:PS2X_NO_AUDIO, $env:PS2X_MUTE)
    $env:PS2X_NO_AUDIO = $null; $env:PS2X_MUTE = $null; Set-RunnerAudio
    Check ($env:PS2X_NO_AUDIO -eq '1' -and $env:PS2X_MUTE -eq '1') "default: no audio device (and muted for older runners)"
    $env:PS2X_NO_AUDIO = $null; $env:PS2X_MUTE = $null; Set-RunnerAudio $true
    Check ($env:PS2X_NO_AUDIO -eq '0' -and $env:PS2X_MUTE -eq '0') "-Sound: device opened, sound on"
    $env:PS2X_NO_AUDIO = $null; $env:PS2X_MUTE = '0'; Set-RunnerAudio
    Check ($env:PS2X_NO_AUDIO -eq '0' -and $env:PS2X_MUTE -eq '0') "a caller's PS2X_MUTE=0 keeps sound on"
    $env:PS2X_NO_AUDIO = $savedAudio[0]; $env:PS2X_MUTE = $savedAudio[1]
    $ps = (Get-Command powershell.exe).Source
    # A dummy headless "runner": no window, polls PS2X_STOP_FILE, says goodbye on stdout, exit 0.
    $fake = Join-Path $tmp "fake.ps1"
    @'
[Console]::Out.WriteLine("[run] headless (PS2X_HEADLESS)"); [Console]::Out.Flush()
while (-not (Test-Path -LiteralPath $env:PS2X_STOP_FILE)) { Start-Sleep -Milliseconds 100 }
[Console]::Out.WriteLine("[run] stop requested (stop file)"); [Console]::Out.Flush()
exit 0
'@ | Set-Content $fake -Encoding utf8
    $stop = New-RunnerStopFile (Join-Path $tmp "fake")
    Set-Content $stop "leftover"; $stop = New-RunnerStopFile (Join-Path $tmp "fake")
    Check (-not (Test-Path $stop) -and $env:PS2X_STOP_FILE -eq $stop) "stop file path set, leftover removed"
    $out = Join-Path $tmp "fake.out.txt"
    $p = Start-Quiet $ps -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $fake `
        -StdOut $out -StdErr (Join-Path $tmp "fake.err.txt")
    for ($i = 0; $i -lt 100 -and -not (Test-RunnerHeadless $out); $i++) { Start-Sleep -Milliseconds 100 }
    Check (Test-RunnerHeadless $out) "headless runner recognised from its stdout"
    $how = Stop-Runner $p $stop 10
    Check ($how -eq "closed gracefully" -and $p.ExitCode -eq 0) "stop file stops a windowless runner cleanly ('$how', exit $($p.ExitCode))"
    Check ((Get-Content $out) -match 'stop requested') "its stdout was flushed"
    Check (-not (Test-Path $stop)) "stop file removed afterwards"
    # A runner that ignores the stop file is killed after the wait.
    $p2 = Start-Quiet $ps -ArgumentList "-NoProfile", "-Command", "Start-Sleep 60" `
        -StdOut (Join-Path $tmp "p2.out.txt") -StdErr (Join-Path $tmp "p2.err.txt")
    $how2 = Stop-Runner $p2 (New-RunnerStopFile (Join-Path $tmp "p2")) 2
    Check ($how2 -eq "force-killed" -and $p2.HasExited) "a runner ignoring it is force-killed ('$how2')"
    Remove-Item Env:PS2X_STOP_FILE -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}
