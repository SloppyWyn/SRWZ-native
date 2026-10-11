# Runs the game, captures the window at the given seconds (overlay hidden), then prints a
# report from the logs: clock drift, VBlank rate, DMA/GIF rates per 2 s window, present
# failures, missing jump targets, error lines, watchdog stall reports, and the categories of
# stderr lines.
#     powershell -File tools\run-report.ps1 -Name run15 -Seconds 52 -Shots 25,48 [-Trace "Cd,Sif"]
#     powershell -File tools\run-report.ps1 -Name run15 -NoRun      (report on existing logs)
# -LogBase <path> reports on <path>.out.txt / .err.txt instead of local\runs\<Name>.*.
# -Headless (PS2X_HEADLESS=1), -Sound (open the audio device; default none, PS2X_NO_AUDIO=1) and
# -Exe <runner> go to run-game.ps1. A headless run (also one the
# runner switched to itself for want of a monitor) has no window: -Shots are skipped then (take
# PS2X_GS_DUMP dumps instead, see tools\long-run.ps1).
# Each distinct missing guest function ([guest-branch:missing-target]; the runtime parks that
# thread quietly) ends the report with a red "MISSING FUNCTION target=..." line and the fix hint
# (tools\missingtargets.ps1).
#     powershell -File tools\run-report.ps1 -SelfTest      (parses sample missing-target lines)
param([string]$Name = "run", [int]$Seconds = 45, [int[]]$Shots = @(), [string]$Trace = "",
      [switch]$NoRun, [string]$LogBase = "", [switch]$Headless, [switch]$Sound, [string]$Exe = "",
      [switch]$SelfTest)
. (Join-Path $PSScriptRoot "missingtargets.ps1")
if ($SelfTest) {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $new = "[guest-branch:missing-target] kind=IndirectCall op=jalr source=0x1bdb24 target=0x1bdb20 pc=0x1bdb20 ra=0x1bd9f8 sp=0x1ffe0 gp=0x0 a0=0x4 a1=0x0 a2=0x0 a3=0x0 s0=0x0 s1=0x0 v0=0x0 v1=0x0 a0Readable=no a0[0]=0x0 codeRegion=yes policy=1 thread=3 sourceName=FUN_001bdb00+0x24 raName=FUN_001bd900+0xf8 trace=0x1bd900 (FUN_001bd900) -> 0x1bdb00 (FUN_001bdb00)"
    $old = "[guest-branch:missing-target] kind=Jump op=j source=0x0 target=0x756780 pc=0x756780 ra=0x18b630 sp=0x1ffe0 gp=0x0 a0=0x0 codeRegion=no policy=1 trace=0x18b600 (0x18b600)"
    $m = @(Get-MissingTargets @("[run:tick] tick=1", $new, "[other] x=1", $new, $old))
    Check ($m.Count -eq 2) "two distinct targets from three missing-target lines ($($m.Count))"
    $l1 = Format-MissingTarget $m[0]; $l2 = Format-MissingTarget $m[1]
    "      $l1"; "      $l2"
    Check ($l1 -match '^MISSING FUNCTION target=0x1bdb20 from source/pc 0x1bdb24 \(FUN_001bdb00\+0x24\) ra 0x1bd9f8 \(FUN_001bd900\+0xf8\) thread 3' -and $l1 -match 'config/entries.txt via tools/codeptrs.py') "new-format line: names, thread, hint"
    Check ($m[0].Count -eq 2) "repeat of the same target counted ($($m[0].Count))"
    Check ($l2 -match '^MISSING FUNCTION target=0x756780 from source/pc 0x756780 \(pc\) ra 0x18b630 thread \?') "old-format line (no thread/names, source 0) falls back to pc"
    Check (@(Get-MissingTargets @("[run:tick] tick=1")).Count -eq 0) "no missing-target lines: nothing"
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}
. (Join-Path $PSScriptRoot "gameconfig.ps1")
. (Join-Path $PSScriptRoot "runstop.ps1")
$root = Get-MainRoot
$runs = Join-Path $root "local\runs"
try { $r = Resolve-GamePath (Get-GameConfig) 'runs'; if ($r) { $runs = $r } } catch { }
if (-not $LogBase) { $LogBase = Join-Path $runs $Name }
if (-not $NoRun) {
# run-game.ps1 in a quiet child (tools\launch.ps1, via runstop.ps1): no console window.
$rgArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "run-game.ps1"), "-Name", $Name, "-Seconds", $Seconds)
if ($Trace) { $rgArgs += @("-Trace", $Trace) }
if ($Headless) { $rgArgs += "-Headless" }
if ($Sound) { $rgArgs += "-Sound" }
if ($Exe) { $rgArgs += @("-Exe", $Exe) }
$rgLog = "$LogBase.run-game.txt"
$job = Start-Quiet (Join-Path $PSHOME "powershell.exe") -ArgumentList $rgArgs -StdOut $rgLog -StdErr $rgLog
$t0 = Get-Date
$first = $true
foreach ($at in $Shots) {
    $wait = $at - ((Get-Date) - $t0).TotalSeconds
    if ($wait -gt 0) { Start-Sleep -Seconds ([int]$wait) }
    if ($Headless -or (Test-RunnerHeadless "$LogBase.out.txt")) { "shots skipped: the runner is headless (no window)"; $Shots = @(); break }
    $png = Join-Path $runs "$Name-$($at)s.png"
    if ($first) { & (Join-Path $PSScriptRoot "screenshot.ps1") -Out $png -HideOverlay; $first = $false }
    else { & (Join-Path $PSScriptRoot "screenshot.ps1") -Out $png }
}
$null = Wait-Quiet $job
Get-Content -LiteralPath $rgLog
Remove-Item -LiteralPath $rgLog -Force -ErrorAction SilentlyContinue
}
$out = Get-Content "$LogBase.out.txt"
$err = Get-Content "$LogBase.err.txt"
""
"== ticks (every 2 s): host ms, guest ms, vsync, DMA/GIF per window"
$prev = $null
foreach ($line in ($out | Where-Object { $_ -match "\[run:tick\]" })) {
    if ($line -match "tick=(\d+) pc=(0x[0-9a-f]+).*activeThreads=(\d+) hostMs=(\d+) eeMs=(\d+) vsync=(\d+) dma=(\d+) gif=(\d+)") {
        $cur = @{ tick = [int]$Matches[1]; pc = $Matches[2]; thr = $Matches[3]; host = [int]$Matches[4]; ee = [int]$Matches[5]; vs = [int]$Matches[6]; dma = [int]$Matches[7]; gif = [int]$Matches[8] }
        if ($prev) {
            $dv = $cur.vs - $prev.vs; $dd = $cur.dma - $prev.dma; $dg = $cur.gif - $prev.gif
            "  t={0,5}s pc={1} thr={2} drift={3,5}ms vsync+{4,3} dma+{5,4} gif+{6,4}" -f [int]($cur.host / 1000), $cur.pc, $cur.thr, ($cur.host - $cur.ee), $dv, $dd, $dg
        }
        $prev = $cur
    }
}
""
"== trouble"
$missing = @(Get-MissingTargets $err)
"  MISSING FUNCTIONS (missing jump targets, distinct): $($missing.Count)$(if ($missing.Count) { ' -- listed at the end' })"
$present = @($err | Where-Object { $_ -match "gs:present:none" })
"  present failures logged: $($present.Count)"
$present | Select-Object -Last 3 | ForEach-Object { "  " + $_.Substring(0, [Math]::Min(300, $_.Length)) }
$priv = @($err | Where-Object { $_ -match "\[gs:priv\]" })
"  privileged GS register changes: $($priv.Count)"
$priv | Select-Object -Last 8 | ForEach-Object { "  " + $_ }
$errors = @($err | Where-Object { $_ -match "error|exception|abort|fatal" -and $_ -notmatch "sceCdGetError|GetError" })
"  error lines: $($errors.Count)"
$errors | Select-Object -First 5 | ForEach-Object { "  " + $_.Substring(0, [Math]::Min(200, $_.Length)) }
$wd = @($err | Where-Object { $_ -match "^\[watchdog\] STALL report" })
"  watchdog stall reports: $($wd.Count)$(if ($wd.Count) { ' (one ~6 s after boot is the boot-time load)' })"
$wd | Select-Object -First 5 | ForEach-Object { "  " + $_.Substring(0, [Math]::Min(200, $_.Length)) }
""
"== stderr line categories (top 15)"
$err | ForEach-Object { if ($_ -match "^(\[[^\]]*\])") { $Matches[1] -replace "[0-9a-fx]{4,}", "" } else { "(other)" } } | Group-Object | Sort-Object Count -Descending | Select-Object -First 15 | ForEach-Object { "  {0,6}  {1}" -f $_.Count, $_.Name }
""
"== last stdout / stderr lines"
$out | Select-Object -Last 3 | ForEach-Object { "  " + $_.Substring(0, [Math]::Min(200, $_.Length)) }
$err | Select-Object -Last 3 | ForEach-Object { "  " + $_.Substring(0, [Math]::Min(200, $_.Length)) }
"logs: $LogBase.out.txt / .err.txt" + $(if ($Shots.Count -gt 0) { "; shots: " + (($Shots | ForEach-Object { "$Name-$($_)s.png" }) -join ", ") } else { "" })
if ($missing.Count) { ""; Write-MissingTargets $missing }
