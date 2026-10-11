# The check after every runtime merge, in one command. Each step prints one summary line
# (PASS/FAIL/SKIP, key numbers, log path); the script stops at the first hard failure.
#     powershell -File tools\verify-merge.ps1 [-Regen] [-Quiet] [-Skip 3,fps] [-KeepGoing]
#     powershell -File tools\verify-merge.ps1 -SelfTest        (plan + parsers on sample logs)
#   0 wait     until no runner from the main build path is running (the build would fail to
#              link over it), then take the shared build lock (tools\lock.ps1) for the builds
#   (-Regen)   make_config.py, the recompiler, overlays.py, under the lock
#   1 build    tools\build-game.bat (main checkout)
#   2 tests    build ps2x_tests, run them from a scratch folder (they create mc0/mc1 in the cwd)
#   3 regress  tools\regress.ps1 -Headless -Seconds 60 (no audio device), verdict
#   4 snapshot re-save the title snapshot (local\runs\snap\title.bin) with the new runner:
#              PS2X_SNAPSHOT_SAVE/AT at the config's title tick, fast-forward; then load it once
#   5 fps      tools\fpsrun.py on the title/menu route (route 'title_menu' via tools\route.py when
#              present, else fpsrun's START at 3000) under the A/B rules (nothing else running,
#              build lock held), headless; game fps, VBlank/s, EE-thread Mcycles per vsync
# -Quiet: this script and everything it starts run at BelowNormal priority on half the logical
# processors (builds and runs alike; fps numbers then are not comparable with a normal run).
# -Skip: step numbers or names (wait, build, tests, regress, snapshot, fps). Paths come from
# config\game.json (runner, ELF, disc, recompiler, runs, checkpoints.<-Checkpoint>.tick).
# Writes local\runs\verify-<timestamp>\summary.txt plus one log per step in that folder.
# Exit code 0 = every step that ran passed.
param(
    [switch]$Regen,
    [switch]$Quiet,
    [string[]]$Skip = @(),
    [switch]$KeepGoing,
    [string]$Checkpoint = "title",
    [int]$SnapTick = 0,
    [string]$SnapFile = "",
    [string]$Route = "title_menu",
    [int]$FpsSeconds = 150,
    [int]$FpsRetries = 1,
    [int]$RegressSeconds = 60,
    [int]$MaxTestFailures = 0,
    [int]$WaitSeconds = 7200,
    [switch]$DryRun,
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "gameconfig.ps1")
. (Join-Path $PSScriptRoot "launch.ps1")
. (Join-Path $PSScriptRoot "runstop.ps1")
# lock.ps1 has a param block: dot-sourcing it resets same-named variables (-SelfTest, -WaitSeconds).
$keep = @{ SelfTest = [bool]$SelfTest; WaitSeconds = $WaitSeconds }
. (Join-Path $PSScriptRoot "lock.ps1")
$SelfTest = $keep.SelfTest; $WaitSeconds = $keep.WaitSeconds

$Steps = @("wait", "build", "tests", "regress", "snapshot", "fps")   # index = step number

function ConvertTo-SkipSet([string[]]$items) {
    $set = @{}
    foreach ($i in @($items | ForEach-Object { "$_" -split ',' } | Where-Object { $_ })) {
        $k = $i.Trim().ToLower()
        $n = 0
        if ([int]::TryParse($k, [ref]$n)) { if ($n -ge 0 -and $n -lt $Steps.Count) { $k = $Steps[$n] } else { throw "-Skip: no step $n" } }
        elseif ($Steps -notcontains $k) { throw "-Skip: no step '$k' (steps: $($Steps -join ', '))" }
        $set[$k] = $true
    }
    $set
}

# ------------------------------------------------------------------ log parsers (self-tested)

function Remove-Ansi([string]$s) { $s -replace "\x1b\[[0-9;]*m", "" }

# ps2x_tests stdout: "Passed: N" / "Failed: N" at the end; a failed test's "[Run]: name" line
# ends in "[Failed]" (runtime log output may sit between, even on the next lines).
function Get-TestResult([string[]]$lines) {
    $r = [ordered]@{ Total = $null; Passed = $null; Failed = $null; FailedNames = @() }
    $lastRun = $null
    foreach ($raw in $lines) {
        $l = Remove-Ansi $raw
        if ($l -match '\[Run\]:\s*(.*)$') { $lastRun = $Matches[1] }
        if ($l -match '\[(Failed|Error)\]') {
            $n = if ($l -match '\[Run\]:\s*(.*?)\s*\[(Failed|Error)\]') { $Matches[1] } else { $lastRun }
            if ($n) { $nn = ($n.Trim() -replace '\s+', ' '); $r.FailedNames += $nn.Substring(0, [Math]::Min(100, $nn.Length)) }
        }
        if ($l -match '^Total Tests:\s*(\d+)') { $r.Total = [int]$Matches[1] }
        if ($l -match '^Passed:\s*(\d+)') { $r.Passed = [int]$Matches[1] }
        if ($l -match '^Failed:\s*(\d+)') { $r.Failed = [int]$Matches[1] }
    }
    [pscustomobject]$r
}

# regress.ps1 output: "verdict: PASS (27/27 dumps pass, ...)" then "REGRESSION PASS|FAIL".
function Get-RegressResult([string[]]$lines) {
    $v = @($lines | Where-Object { $_ -match '^verdict:' } | Select-Object -Last 1)
    $r = @($lines | Where-Object { $_ -match '^REGRESSION (PASS|FAIL)' } | Select-Object -Last 1)
    $stall = @($lines | Where-Object { $_ -match '(?i)stall' } | Select-Object -Last 1)
    [pscustomobject]@{
        Pass    = [bool]($r.Count -and $r[0] -match 'PASS')
        Found   = [bool]$r.Count
        Verdict = if ($v.Count) { ($v[0] -replace '^verdict:\s*', '') } else { "" }
        Stall   = if ($stall.Count) { $stall[0] } else { "" }
    }
}

# Runner stderr: "[snapshot] saved <path> tick=N bytes=B ms=M", "[snapshot] loaded <path> tick=N
# ms=M", "[snapshot] save failed at tick ...", "[snapshot] cannot load ...", "still waiting ...".
function Get-SnapshotResult([string[]]$lines) {
    $r = [ordered]@{ Saved = $false; SaveTick = 0; Bytes = 0; SaveMs = 0; Loaded = $false; LoadTick = 0; LoadMs = 0; Error = ""; Waiting = "" }
    foreach ($l in $lines) {
        if ($l -match '\[snapshot\] saved .* tick=(\d+) bytes=(\d+) ms=(\d+)') { $r.Saved = $true; $r.SaveTick = [int]$Matches[1]; $r.Bytes = [int64]$Matches[2]; $r.SaveMs = [int]$Matches[3] }
        elseif ($l -match '\[snapshot\] loaded .* tick=(\d+) ms=(\d+)') { $r.Loaded = $true; $r.LoadTick = [int]$Matches[1]; $r.LoadMs = [int]$Matches[2] }
        elseif ($l -match '\[snapshot\] (save failed.*|cannot load.*)$') { $r.Error = $Matches[1] }
        elseif ($l -match '\[snapshot\] (still waiting.*)$') { $r.Waiting = $Matches[1] }
    }
    [pscustomobject]$r
}

# Last [run:tick] vsync in a runner stdout (plain or fpsrun-stamped lines).
function Get-LastVsync([string[]]$lines) {
    $v = 0
    foreach ($l in $lines) { if ($l -match '\[run:tick\].*\bvsync=(\d+)') { $v = [int]$Matches[1] } }
    $v
}

# fpsrun.py logs: every line "<host s> <text>". Samples: "t,tid:cycles:cpuMs tid:..." (host s
# since the runner started, the per-thread QueryThreadCycleTime and CPU time).
function Split-Stamped([string[]]$lines) {
    foreach ($l in $lines) { if ($l -match '^\s*([\d.]+) (.*)$') { [pscustomobject]@{ T = [double]$Matches[1]; L = $Matches[2] } } }
}

function Read-Samples([string[]]$lines) {
    foreach ($l in $lines) {
        $p = $l -split ',', 2
        if ($p.Count -lt 2 -or $p[0] -notmatch '^[\d.]+$') { continue }
        $th = @{}
        foreach ($x in ($p[1] -split ' ' | Where-Object { $_ })) { $q = $x -split ':'; $th[[int]$q[0]] = @([double]$q[1], [double]$q[2]) }
        [pscustomobject]@{ T = [double]$p[0]; Th = $th }
    }
}

function Get-VsyncAt($ticks, [double]$t) {
    for ($i = 0; $i -lt $ticks.Count - 1; $i++) {
        $a = $ticks[$i]; $b = $ticks[$i + 1]
        if ($a.T -le $t -and $t -le $b.T -and $b.T -gt $a.T) { return $a.V + ($b.V - $a.V) * ($t - $a.T) / ($b.T - $a.T) }
    }
    $null
}

# One window [lo, hi] (host s): game fps (scePadRead calls/s), VBlank/s, busiest thread's
# Mcycles per vsync and its saturation (CPU time / wall time).
function Measure-FpsWindow($reads, $ticks, $samples, [double]$lo, [double]$hi) {
    if ($ticks.Count -lt 2) { return $null }
    $lo = [Math]::Max($lo, $ticks[0].T); $hi = [Math]::Min($hi, $ticks[-1].T)
    if ($hi -le $lo) { return $null }
    $n = @($reads | Where-Object { $_ -ge $lo -and $_ -lt $hi }).Count
    $r = [ordered]@{ Lo = $lo; Hi = $hi; Fps = $n / ($hi - $lo); Vbl = ((Get-VsyncAt $ticks $hi) - (Get-VsyncAt $ticks $lo)) / ($hi - $lo); Mcyc = $null; Sat = $null; V0 = [int](Get-VsyncAt $ticks $lo); V1 = [int](Get-VsyncAt $ticks $hi) }
    $w = @($samples | Where-Object { $_.T -ge $lo -and $_.T -le $hi })
    if ($w.Count -ge 2) {
        $a = $w[0]; $b = $w[-1]
        $va = Get-VsyncAt $ticks $a.T; $vb = Get-VsyncAt $ticks $b.T
        $best = $null; $bc = -1.0
        foreach ($k in $b.Th.Keys) { if ($a.Th.ContainsKey($k)) { $d = $b.Th[$k][0] - $a.Th[$k][0]; if ($d -gt $bc) { $bc = $d; $best = $k } } }
        if ($null -ne $best -and $null -ne $va -and $null -ne $vb -and $vb -gt $va) {
            $r.Mcyc = $bc / 1e6 / ($vb - $va)
            $r.Sat = ($b.Th[$best][1] - $a.Th[$best][1]) / 1000.0 / ($b.T - $a.T)
        }
    }
    [pscustomobject]$r
}

function Get-FpsResult([string[]]$outLines, [string[]]$errLines, [string[]]$sampleLines, [double[]]$titleWin = @(0.5, 5), [double]$menuFrom = 10) {
    $out = @(Split-Stamped $outLines); $err = @(Split-Stamped $errLines)
    $ticks = @($out | ForEach-Object { if ($_.L -match '\[run:tick\].*\bvsync=(\d+)') { [pscustomobject]@{ T = $_.T; V = [int]$Matches[1] } } })
    $reads = @($err | Where-Object { $_.L.StartsWith("[stub] scePadRead@") -and $_.L.Contains("(") } | ForEach-Object { $_.T })
    $start = @($out + $err | Where-Object { $_.L -match '^\[pad:script\] tick=' } | Sort-Object T | Select-Object -First 1)
    $samples = @(Read-Samples $sampleLines)
    $r = [ordered]@{ Start = $null; Title = $null; Menu = $null; Reads = $reads.Count; Ticks = $ticks.Count }
    if (-not $start.Count) { return [pscustomobject]$r }
    $s = $start[0].T; $r.Start = $s
    $r.Title = Measure-FpsWindow $reads $ticks $samples ($s + $titleWin[0]) ($s + $titleWin[1])
    $r.Menu = Measure-FpsWindow $reads $ticks $samples ($s + $menuFrom) 1e9
    [pscustomobject]$r
}

function Format-FpsWindow($name, $w) {
    if (-not $w) { return "$name n/a" }
    $c = if ($null -ne $w.Mcyc) { "{0:F1} Mcyc/vsync (sat {1:F2})" -f $w.Mcyc, $w.Sat } else { "Mcyc n/a" }
    "{0} {1:F1} fps, VBlank {2:F1}/s, {3}" -f $name, $w.Fps, $w.Vbl, $c
}

# ------------------------------------------------------------------ helpers

function Get-OtherLoad([int[]]$exceptPids = @()) {
    $p = @(Get-Process ps2EntryRunner, cl, link -ErrorAction SilentlyContinue | Where-Object { $exceptPids -notcontains $_.Id })
    @($p | ForEach-Object { "$($_.ProcessName)#$($_.Id)" })
}

function Get-MainRunnerProcs([string]$runnerPath) {
    @(Get-Process ps2EntryRunner -ErrorAction SilentlyContinue | Where-Object {
        $path = $null; try { $path = $_.Path } catch { }
        $path -and ([IO.Path]::GetFullPath($path) -ieq [IO.Path]::GetFullPath($runnerPath))
    })
}

if (-not ('VerifyMerge.Native' -as [type])) {
    Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;
namespace VerifyMerge {
public static class Native {
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr OpenThread(uint access, bool inherit, uint tid);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool QueryThreadCycleTime(IntPtr h, out ulong cycles);
    [DllImport("kernel32.dll", SetLastError = true)] static extern bool CloseHandle(IntPtr h);
    public static ulong Cycles(uint tid) {
        IntPtr h = OpenThread(0x0800, false, tid);   // THREAD_QUERY_LIMITED_INFORMATION
        if (h == IntPtr.Zero) return 0;
        ulong c = 0; QueryThreadCycleTime(h, out c); CloseHandle(h); return c;
    }
}
}
'@
}

# ------------------------------------------------------------------ self-test

if ($SelfTest) {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $e = [char]27
    $t = Get-TestResult @("$e[33m  [Run]: $e[0mfirst test $e[32m  [Passed]$e[0m",
        "$e[33m  [Run]: $e[0mGS thing [gs:copy-reg] reg=0x0", "$e[31m  [Failed]$e[0m",
        "$e[33m  [Run]: $e[0mstub abi $e[31m  [Failed]$e[0m", "", "========================================",
        "Total Tests: 3", "Passed: 1", "Failed: 2", "========================================")
    Check ($t.Total -eq 3 -and $t.Passed -eq 1 -and $t.Failed -eq 2) "ps2x_tests counts ($($t.Passed)/$($t.Total), failed $($t.Failed))"
    Check ($t.FailedNames.Count -eq 2 -and $t.FailedNames[0] -like 'GS thing*' -and $t.FailedNames[1] -eq 'stub abi') "failed test names ($($t.FailedNames -join ' | '))"
    $g = Get-RegressResult @("PASS  tick 1200 screen", "verdict: PASS (27/27 dumps pass, 0 fail, 0 outside the baseline; content order, threshold 10.0)", "contact sheet: x", "REGRESSION PASS")
    Check ($g.Pass -and $g.Verdict -like 'PASS (27/27*') "regress PASS verdict"
    $g = Get-RegressResult @("verdict: FAIL (25/27 dumps pass, 2 fail, 0 outside the baseline)", "REGRESSION FAIL")
    Check (-not $g.Pass -and $g.Found) "regress FAIL verdict"
    Check (-not (Get-RegressResult @("garbage")).Found) "regress output without a verdict"
    $s = Get-SnapshotResult @("[snapshot] armed: x", "[snapshot] still waiting for a safe point since tick 27250 (busy)",
        "[snapshot] saved C:\x\title.bin tick=27251 bytes=47645068 ms=92")
    Check ($s.Saved -and $s.SaveTick -eq 27251 -and $s.Bytes -eq 47645068 -and $s.SaveMs -eq 92) "snapshot saved line"
    $s = Get-SnapshotResult @("[snapshot] loaded C:\x\title.bin tick=27251 ms=267")
    Check ($s.Loaded -and $s.LoadTick -eq 27251 -and $s.LoadMs -eq 267) "snapshot loaded line"
    $s = Get-SnapshotResult @("[snapshot] cannot load C:\x\title.bin: built by another runner")
    Check (-not $s.Loaded -and $s.Error -like 'cannot load*') "snapshot load error"
    Check ((Get-LastVsync @("[run:tick] tick=1 hostMs=5 eeMs=1 vsync=100 dma=0", "x", "  2.000 [run:tick] tick=2 hostMs=9 vsync=220 dma=0")) -eq 220) "last vsync"
    # Synthetic fpsrun logs: START at 50 s; 60 VBlank/s; 30 reads/s in the title, 45 in the menu;
    # busiest thread 1e9 cycles/s and 0.9 CPU s/s, an idle one next to it.
    $out = @("   0.100 Using argv boot path")
    for ($i = 0; $i -le 40; $i++) { $out += ("{0,9:F3} [run:tick] tick={1} hostMs={2} eeMs=0 vsync={3} dma=0 eeCpuMs=0" -f (40 + 2 * $i), $i, ((40 + 2 * $i) * 1000), (60 * (40 + 2 * $i))) }
    $err = @("  50.000 [pad:script] tick=3001 buttons=START")
    for ($x = 50.0; $x -lt 120; $x += 0.01) {
        $rate = if ($x -lt 60) { 30 } else { 45 }
        if ([Math]::Floor($x * $rate) -ne [Math]::Floor(($x - 0.01) * $rate)) { $err += ("{0,9:F3} [stub] scePadRead@0x1309b0(a0=0x0) ra=0x18d3ec" -f $x) }
    }
    $err += "  51.000 [stub] scePadRead@0x1309b0 -> v0=0x1"
    $smp = @(for ($x = 40.0; $x -le 120; $x += 0.5) { "{0:F2},11:{1}:{2} 12:{3}:{4}" -f $x, [int64](1e9 * $x), [int64](900 * $x), [int64](1e6 * $x), [int64](10 * $x) })
    $f = Get-FpsResult $out $err $smp
    Check ($f.Start -eq 50) "fps: START found at 50 s"
    Check ($f.Title -and [Math]::Abs($f.Title.Fps - 30) -lt 1 -and [Math]::Abs($f.Title.Vbl - 60) -lt 0.01) "fps: title window $(Format-FpsWindow 'title' $f.Title)"
    Check ($f.Menu -and [Math]::Abs($f.Menu.Fps - 45) -lt 1 -and [Math]::Abs($f.Menu.Mcyc - 1000 / 60.0) -lt 0.1 -and [Math]::Abs($f.Menu.Sat - 0.9) -lt 0.01) "fps: menu window $(Format-FpsWindow 'menu' $f.Menu)"
    Check ($null -eq (Get-FpsResult $out @() $smp).Start) "fps: no START -> no windows"
    $k = ConvertTo-SkipSet @("1,tests", "5")
    Check ($k.build -and $k.tests -and $k.fps -and -not $k.regress) "-Skip parsing (numbers and names)"
    $bad = $false; try { $null = ConvertTo-SkipSet @("nonsense") } catch { $bad = $true }
    Check $bad "-Skip rejects unknown steps"
    "-- dry-run plan --"
    $plan = @(Invoke-Quiet powershell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $PSCommandPath, "-DryRun", "-Regen", "-Quiet") -MergeErr)
    $plan | ForEach-Object { "   $_" }
    Check (@($plan | Where-Object { $_ -match '^\[dry-run\] [0-5] ' }).Count -ge 6 -and ($plan -join "`n") -notmatch '(?i)exception|error') "dry-run plan lists every step ($(@($plan | Where-Object { $_ -match '^\[dry-run\] [0-5] ' }).Count) lines)"
    if ($ok) { "selftest passed"; exit 0 } else { "selftest FAILED"; exit 1 }
}

# ------------------------------------------------------------------ setup

$skipSet = ConvertTo-SkipSet $Skip
$cfg = Get-GameConfig
$root = $cfg._root
$tools = Join-Path $root "tools"
$runner = Resolve-GamePath $cfg 'runner'
$elf = Resolve-GamePath $cfg 'elf'
$disc = Resolve-GamePath $cfg 'disc'
$workdir = Resolve-GamePath $cfg 'workdir'; if (-not $workdir) { $workdir = Split-Path -Parent $elf }
$runs = Resolve-GamePath $cfg 'runs'
$recompiler = Resolve-GamePath $cfg 'recompiler'
$recompToml = Resolve-GamePath $cfg 'recompToml'
$buildDir = Split-Path -Parent (Split-Path -Parent (Split-Path -Parent $runner))   # <build>\ps2xRuntime\Release\<runner>.exe
$testsExe = Join-Path $buildDir "ps2xTest\Release\ps2x_tests.exe"
$cp = $cfg.checkpoints.PSObject.Properties[$Checkpoint]
if (-not $cp) { throw "checkpoint '$Checkpoint' not in $($cfg._path)" }
if (-not $SnapTick) { $SnapTick = [int]$cp.Value.tick }
if (-not $SnapFile) { $SnapFile = Join-Path $runs "snap\$Checkpoint.bin" }
$ff = "4"
$ffFeat = $cfg.runner.features.PSObject.Properties['fastforward']
if ($ffFeat -and $ffFeat.Value.value) { $ff = "$($ffFeat.Value.value)" }
$ffRate = 4.0; [double]::TryParse($ff, [ref]$ffRate) | Out-Null; if ($ffRate -le 0) { $ffRate = 4.0 }
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$vdir = Join-Path $runs "verify-$stamp"
$summaryFile = Join-Path $vdir "summary.txt"
# Builds use the main checkout's scripts (build-game.bat builds the checkout it lives in); the run
# and scoring tools are this checkout's (the same files once this is on main).
$here = $PSScriptRoot
$fpsrun = Join-Path $here "fpsrun.py"
$haveRoute = [bool]$Route -and (Test-Path (Join-Path $here "route.py")) -and [bool](Select-String -LiteralPath $fpsrun -Pattern '--route' -SimpleMatch -Quiet)
$nestedLock = [bool](Select-String -Path (Join-Path $tools "lock.ps1") -Pattern 'SRWZ_LOCK_HELD_' -SimpleMatch -Quiet)
$halfMask = $null
if ($Quiet) {
    $n = [Environment]::ProcessorCount; $h = [int][Math]::Max(1, [Math]::Floor($n / 2))
    $halfMask = (([int64]1 -shl $n) - 1) -bxor (([int64]1 -shl ($n - $h)) - 1)   # the upper half
}

if ($DryRun) {
    $q = if ($Quiet) { " (quiet: BelowNormal, affinity 0x{0:X} = {1} of {2} logical processors)" -f $halfMask, $h, $n } else { "" }
    "[dry-run] main root $root; runner $runner; results $vdir$q"
    "[dry-run] 0 wait: until no ps2EntryRunner runs from $runner (up to $WaitSeconds s), then lock 'build'" + $(if ($skipSet.wait) { " [skipped]" } else { "" })
    if ($Regen) {
        $tdir = Split-Path -Parent $recompToml
        "[dry-run] - regen: python $tools\make_config.py $tdir\ghidra.toml $tdir\analyzer.toml $recompToml; $recompiler $recompToml; python $tools\overlays.py (under the lock)"
    }
    "[dry-run] 1 build: $tools\build-game.bat Release" + $(if ($nestedLock) { " (lock kept: nested)" } else { " (main lock.ps1 has no nesting: lock released for it, retaken after)" }) + $(if ($skipSet.build) { " [skipped]" } else { "" })
    "[dry-run] 2 tests: cmake --build $buildDir --target ps2x_tests; $testsExe in $vdir\tests (pass: failed <= $MaxTestFailures)" + $(if ($skipSet.tests) { " [skipped]" } else { "" })
    "[dry-run] 3 regress: $here\regress.ps1 -Name vm$stamp -Headless -Seconds $RegressSeconds (PS2X_NO_AUDIO=1)" + $(if ($skipSet.regress) { " [skipped]" } else { "" })
    "[dry-run] 4 snapshot: save at tick $SnapTick (fast-forward $ff, headless, exit after) -> $SnapFile; load it once" + $(if ($skipSet.snapshot) { " [skipped]" } else { "" })
    $rt = if ($haveRoute) { "--route $Route" } else { "fpsrun default START at 3000" }
    "[dry-run] 5 fps: $fpsrun $FpsSeconds s headless, $rt, lock held, nothing else running; retries $FpsRetries" + $(if ($skipSet.fps) { " [skipped]" } else { "" })
    exit 0
}

New-Item -ItemType Directory -Force $vdir | Out-Null
$results = New-Object System.Collections.Generic.List[string]
$script:failed = $false
function Out-Step([string]$line) {
    $results.Add($line); $line
    Add-Content -LiteralPath $summaryFile -Value $line
}
function Stop-IfHard([bool]$pass) {
    if (-not $pass) {
        $script:failed = $true
        if (-not $KeepGoing) { Out-Step "stopped after the first hard failure (-KeepGoing runs on)"; throw "verify-merge: hard failure" }
    }
}

Set-Content -LiteralPath $summaryFile -Value ("verify-merge {0}  main {1}  runner {2}{3}" -f (Get-Date -Format s), $root, $runner, $(if ($Quiet) { "  QUIET (BelowNormal, half the cores: fps not comparable)" } else { "" }))
$head = (& git -C $root log -1 --format="%h %s" 2>$null)
$sub = (& git -C (Join-Path $root "third_party\PS2Recomp") log -1 --format="%h %s" 2>$null)
Add-Content -LiteralPath $summaryFile -Value "main $head`nPS2Recomp $sub"
"verify-merge -> $vdir"

if ($Quiet) {
    $me = [Diagnostics.Process]::GetCurrentProcess()
    $me.ProcessorAffinity = [IntPtr]$halfMask        # children inherit the mask...
    $me.PriorityClass = 'BelowNormal'                # ...and the BelowNormal class
    $env:SRWZ_LAUNCH_PRIORITY = 'BelowNormal'
    $env:CMAKE_BUILD_PARALLEL_LEVEL = "$h"
}

$lockHeld = $false
function Enter-BuildLock([string]$why) {
    if ($script:lockHeld) { return }
    if (-not (Enter-NamedLock build -Owner "verify-merge $why" -WaitSeconds $WaitSeconds)) { throw "build lock: timed out after $WaitSeconds s" }
    $script:lockHeld = $true
    $env:SRWZ_LOCK_HELD_BUILD = "$PID"   # lets a nested lock.ps1 (build-game.bat) through
}
function Exit-BuildLock {
    if (-not $script:lockHeld) { return }
    $null = Exit-NamedLock build
    $script:lockHeld = $false
    $env:SRWZ_LOCK_HELD_BUILD = $null
}

function Wait-NoMainRunner {
    $t0 = Get-Date; $said = $false
    while ($true) {
        $p = @(Get-MainRunnerProcs $runner)
        if (-not $p.Count) { return [int]((Get-Date) - $t0).TotalSeconds }
        if (-not $said) { Write-Host "waiting: runner in use by pid $(@($p | ForEach-Object Id) -join ',')"; $said = $true }
        if (((Get-Date) - $t0).TotalSeconds -gt $WaitSeconds) { throw "runner still in use after $WaitSeconds s" }
        Start-Sleep -Seconds 5
    }
}

function Invoke-Logged([string]$exe, [string[]]$a, [string]$log, [string]$wd = $root, [int]$timeout = 0) {
    $null = Invoke-Quiet $exe $a -WorkingDirectory $wd -MergeErr -StdOut $log -TimeoutSeconds $timeout
    $LASTEXITCODE
}

$tAll = Get-Date
try {
    # ---- 0 wait + lock
    if (-not $skipSet.wait) {
        $w = Wait-NoMainRunner
        $needLock = $Regen -or -not $skipSet.build -or -not $skipSet.tests
        if ($needLock) { $t0 = Get-Date; Enter-BuildLock "builds"; $lw = [int]((Get-Date) - $t0).TotalSeconds } else { $lw = 0 }
        Out-Step ("0 wait     PASS  runner free after {0} s{1}" -f $w, $(if ($needLock) { ", build lock after $lw s" } else { "" }))
    } else { Out-Step "0 wait     SKIP" }

    # ---- regen
    if ($Regen) {
        Enter-BuildLock "regen"
        $t0 = Get-Date; $log = Join-Path $vdir "regen.txt"; $tdir = Split-Path -Parent $recompToml
        $rc = Invoke-Logged python @((Join-Path $tools "make_config.py"), (Join-Path $tdir "ghidra.toml"), (Join-Path $tdir "analyzer.toml"), $recompToml) $log
        if ($rc -eq 0) { $rc = Invoke-Logged $recompiler @($recompToml) "$log.recomp.txt" }
        if ($rc -eq 0) { $rc = Invoke-Logged python @((Join-Path $tools "overlays.py")) "$log.overlays.txt" }
        Out-Step ("- regen    {0}  make_config + recompiler + overlays, {1} s, exit {2}  {3}*" -f $(if ($rc -eq 0) { "PASS" } else { "FAIL" }), [int]((Get-Date) - $t0).TotalSeconds, $rc, $log)
        Stop-IfHard ($rc -eq 0)
    }

    # ---- 1 build
    if (-not $skipSet.build) {
        if (-not $skipSet.wait) { $null = Wait-NoMainRunner }
        if ($script:lockHeld -and -not $nestedLock) { Exit-BuildLock }   # build-game.bat takes it itself
        $t0 = Get-Date; $log = Join-Path $vdir "build.txt"
        $rc = Invoke-Logged (Join-Path $tools "build-game.bat") @("Release") $log
        $warn = @(Select-String -LiteralPath $log -Pattern ' error ' -SimpleMatch -ErrorAction SilentlyContinue).Count
        $exeT = if (Test-Path $runner) { (Get-Item $runner).LastWriteTime.ToString("HH:mm:ss") } else { "missing" }
        Out-Step ("1 build    {0}  {1} s, exit {2}, {3} error lines, runner {4}  {5}" -f $(if ($rc -eq 0) { "PASS" } else { "FAIL" }), [int]((Get-Date) - $t0).TotalSeconds, $rc, $warn, $exeT, $log)
        Stop-IfHard ($rc -eq 0)
    } else { Out-Step "1 build    SKIP" }

    # ---- 2 tests
    if (-not $skipSet.tests) {
        Enter-BuildLock "tests"
        $t0 = Get-Date; $log = Join-Path $vdir "tests-build.txt"
        $bat = Join-Path $vdir "build-tests.bat"
        $vcvars = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
        $par = if ($Quiet) { "--parallel $h" } else { "--parallel" }
        Set-Content -LiteralPath $bat -Encoding ascii -Value @(
            "@echo off",
            "call `"$vcvars`" >nul 2>&1 || exit /b 1",
            "`"C:\Program Files\CMake\bin\cmake.exe`" --build `"$buildDir`" --config Release $par --target ps2x_tests || exit /b 1")
        $rc = Invoke-Logged $bat @() $log
        $line = $null
        if ($rc -ne 0) { $line = ("2 tests    FAIL  ps2x_tests build exit {0}  {1}" -f $rc, $log) }
        Exit-BuildLock
        if (-not $line) {
            $tdir = Join-Path $vdir "tests"; New-Item -ItemType Directory -Force $tdir | Out-Null
            $tlog = Join-Path $vdir "tests.txt"
            $rc = Invoke-Logged $testsExe @() $tlog $tdir 1800
            $t = Get-TestResult (Get-Content -LiteralPath $tlog)
            $pass = $null -ne $t.Failed -and $t.Failed -le $MaxTestFailures
            $names = if ($t.FailedNames.Count) { " [" + (($t.FailedNames | Select-Object -First 4) -join "; ") + "]" } else { "" }
            $line = ("2 tests    {0}  Passed {1} / Failed {2} of {3}, {4} s{5}  {6}" -f $(if ($pass) { "PASS" } else { "FAIL" }), $t.Passed, $t.Failed, $t.Total, [int]((Get-Date) - $t0).TotalSeconds, $names, $tlog)
        } else { $pass = $false }
        Out-Step $line
        Stop-IfHard $pass
    } else { Out-Step "2 tests    SKIP" }
    Exit-BuildLock

    # ---- 3 regress
    if (-not $skipSet.regress) {
        $t0 = Get-Date; $log = Join-Path $vdir "regress.txt"; $name = "vm$stamp"
        $rc = Invoke-Logged powershell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $here "regress.ps1"), "-Name", $name, "-Headless", "-Seconds", "$RegressSeconds") $log -timeout ($RegressSeconds * 4 + 300)
        $g = Get-RegressResult (Get-Content -LiteralPath $log)
        $vs = Get-LastVsync (Get-Content -LiteralPath (Join-Path $runs "$name.out.txt") -ErrorAction SilentlyContinue)
        $v = if ($g.Found) { $g.Verdict } else { "no verdict (exit $rc)" }
        Out-Step ("3 regress  {0}  {1}; last vsync {2}, {3} s  {4}" -f $(if ($g.Pass) { "PASS" } else { "FAIL" }), $v, $vs, [int]((Get-Date) - $t0).TotalSeconds, $log)
        Stop-IfHard $g.Pass
    } else { Out-Step "3 regress  SKIP" }

    # ---- 4 snapshot
    if (-not $skipSet.snapshot) {
        $t0 = Get-Date
        $tmpSnap = Join-Path $vdir "$Checkpoint.bin"
        $base = Join-Path $vdir "snap-save"
        $stop = "$base.stop"; Remove-Item -LiteralPath $stop -Force -ErrorAction SilentlyContinue
        $env1 = @{ PS2X_HEADLESS = "1"; PS2X_NO_AUDIO = "1"; PS2X_MUTE = "1"; PS2X_FASTFORWARD = $ff; PS2X_STOP_FILE = $stop
                   PS2X_SNAPSHOT_SAVE = $tmpSnap; PS2X_SNAPSHOT_AT = "$SnapTick"; PS2X_SNAPSHOT_EXIT = "1"
                   PS2X_SNAPSHOT_LOAD = $null; PS2X_PAD_SCRIPT = $null; PS2X_GS_DUMP = $null; PS2X_TRACE_STUBS = $null }
        $limit = [int]($SnapTick / 60.0 / $ffRate * 3) + 120
        $p = Start-Quiet $runner -ArgumentList @($elf, $disc) -WorkingDirectory $workdir -StdOut "$base.out.txt" -StdErr "$base.err.txt" -Env $env1 -Window Hidden
        if (-not $p.WaitForExit($limit * 1000)) { $how = Stop-Runner $p $stop; $how = "timeout $limit s, $how" } else { $how = "exit $($p.ExitCode)" }
        $s = Get-SnapshotResult (Get-Content -LiteralPath "$base.err.txt" -ErrorAction SilentlyContinue)
        $saveS = [int]((Get-Date) - $t0).TotalSeconds
        $ok = $s.Saved -and (Test-Path $tmpSnap)
        $loadTxt = ""
        if ($ok) {
            $lbase = Join-Path $vdir "snap-load"; $lstop = "$lbase.stop"; Remove-Item -LiteralPath $lstop -Force -ErrorAction SilentlyContinue
            $env2 = $env1.Clone(); $env2.PS2X_SNAPSHOT_SAVE = $null; $env2.PS2X_SNAPSHOT_AT = $null; $env2.PS2X_SNAPSHOT_EXIT = $null
            $env2.PS2X_SNAPSHOT_LOAD = $tmpSnap; $env2.PS2X_STOP_FILE = $lstop
            $p = Start-Quiet $runner -ArgumentList @($elf, $disc) -WorkingDirectory $workdir -StdOut "$lbase.out.txt" -StdErr "$lbase.err.txt" -Env $env2 -Window Hidden
            $sw = [Diagnostics.Stopwatch]::StartNew(); $l = $null; $vs = 0
            while (-not $p.HasExited -and $sw.Elapsed.TotalSeconds -lt 90) {
                Start-Sleep -Milliseconds 500
                $l = Get-SnapshotResult (Get-Content -LiteralPath "$lbase.err.txt" -ErrorAction SilentlyContinue)
                $vs = Get-LastVsync (Get-Content -LiteralPath "$lbase.out.txt" -ErrorAction SilentlyContinue)
                if ($l.Error -or ($l.Loaded -and $vs -ge $l.LoadTick + 120)) { break }
            }
            $how2 = Stop-Runner $p $lstop
            $l = Get-SnapshotResult (Get-Content -LiteralPath "$lbase.err.txt" -ErrorAction SilentlyContinue)
            $vs = Get-LastVsync (Get-Content -LiteralPath "$lbase.out.txt" -ErrorAction SilentlyContinue)
            $ok = $l.Loaded -and -not $l.Error -and $vs -gt $l.LoadTick
            $loadTxt = if ($l.Loaded) { "; loads in $($l.LoadMs) ms, ran on to vsync $vs ($how2)" } else { "; load FAILED: $($l.Error) ($how2)" }
            if ($ok) {
                New-Item -ItemType Directory -Force (Split-Path -Parent $SnapFile) | Out-Null
                Copy-Item -LiteralPath $tmpSnap -Destination $SnapFile -Force
                Remove-Item -LiteralPath $tmpSnap -Force
            }
        }
        $what = if ($s.Saved) { "saved tick {0}, {1:F1} MB, {2} s ({3})" -f $s.SaveTick, ($s.Bytes / 1MB), $saveS, $how } else { "not saved ($how; $($s.Error)$($s.Waiting))" }
        Out-Step ("4 snapshot {0}  {1}{2} -> {3}  {4}.*" -f $(if ($ok) { "PASS" } else { "FAIL" }), $what, $loadTxt, $(if ($ok) { $SnapFile } else { "(not replaced)" }), $base)
        Stop-IfHard $ok
    } else { Out-Step "4 snapshot SKIP" }

    # ---- 5 fps (A/B rules: nothing else running, build lock held for the whole measurement)
    if (-not $skipSet.fps) {
        $rtName = if ($haveRoute) { "route $Route" } else { "fpsrun START at 3000" }
        $attempt = 0; $done = $false
        while (-not $done) {
            $attempt++
            Enter-BuildLock "fps"
            $t0 = Get-Date
            while (@(Get-OtherLoad).Count) {
                if (((Get-Date) - $t0).TotalSeconds -gt $WaitSeconds) { throw "fps: other runners/compilers still running after $WaitSeconds s: $(Get-OtherLoad)" }
                Start-Sleep -Seconds 5
            }
            $base = Join-Path $vdir "fps$attempt"
            $stop = "$base.stop"; Remove-Item -LiteralPath $stop -Force -ErrorAction SilentlyContinue
            $envF = @{ PS2X_HEADLESS = "1"; PS2X_NO_AUDIO = "1"; PS2X_MUTE = "1"; PS2X_STOP_FILE = $stop
                       PS2X_FASTFORWARD = $null; PS2X_SNAPSHOT_LOAD = $null; PS2X_SNAPSHOT_SAVE = $null; PS2X_GS_DUMP = $null; PS2X_REALTIME_CLOCK = $null; PS2X_PAD_SCRIPT = $null }
            $fa = @($fpsrun, "--exe", $runner, "--elf", $elf, "--iso", $disc, "--wd", $workdir, "--out", $base,
                    "--seconds", "$($FpsSeconds + 20)", "--window", "0:$FpsSeconds")
            if ($haveRoute) { $fa += @("--route", $Route, "--game", $cfg._path) }
            $py = Start-Quiet python -ArgumentList $fa -WorkingDirectory $vdir -StdOut "$base.driver.txt" -StdErr "$base.driver.err.txt" -Env $envF -Window Hidden
            $rp = $null; $sw = [Diagnostics.Stopwatch]::StartNew()
            while (-not $rp -and -not $py.HasExited -and $sw.Elapsed.TotalSeconds -lt 60) {
                Start-Sleep -Milliseconds 100
                $c = Get-CimInstance Win32_Process -Filter "ParentProcessId=$($py.Id) and Name='ps2EntryRunner.exe'" -ErrorAction SilentlyContinue | Select-Object -First 1
                if ($c) { try { $rp = Get-Process -Id $c.ProcessId } catch { } }
            }
            $samples = "$base.samples.csv"
            "hostS,tid:cycles:cpuMs ..." | Set-Content -LiteralPath $samples
            $others = @{}; $lastO = -99.0
            if ($rp) {
                $st = $rp.StartTime
                while (-not $rp.HasExited) {
                    Start-Sleep -Milliseconds 250
                    $t = ((Get-Date) - $st).TotalSeconds
                    if ($t -ge $FpsSeconds) { Set-Content -LiteralPath $stop -Value stop; break }
                    try {
                        $rp.Refresh()
                        $th = @(foreach ($x in $rp.Threads) { "{0}:{1}:{2}" -f $x.Id, [VerifyMerge.Native]::Cycles([uint32]$x.Id), [int64]$x.TotalProcessorTime.TotalMilliseconds })
                    } catch { continue }
                    Add-Content -LiteralPath $samples -Value ("{0:F3},{1}" -f $t, ($th -join ' '))
                    if ($t - $lastO -ge 2) { $lastO = $t; foreach ($o in (Get-OtherLoad @($rp.Id))) { $others[$o] = $true } }
                }
            }
            $null = $py.WaitForExit(($FpsSeconds + 60) * 1000)
            if (-not $py.HasExited) { Stop-QuietTree $py.Id }
            Exit-BuildLock
            Remove-Item -LiteralPath $stop -Force -ErrorAction SilentlyContinue
            $f = Get-FpsResult (Get-Content -LiteralPath "$base.out.txt" -ErrorAction SilentlyContinue) (Get-Content -LiteralPath "$base.err.txt" -ErrorAction SilentlyContinue) (Get-Content -LiteralPath $samples)
            $dirty = $others.Count -gt 0
            if ($dirty -and $attempt -le $FpsRetries) { "fps run $attempt discarded: $($others.Keys -join ', ') ran meanwhile; redoing"; continue }
            $done = $true
            $ok = $null -ne $f.Start -and $f.Menu
            $tag = if (-not $ok) { "FAIL" } elseif ($dirty) { "DIRTY" } else { "PASS" }
            $sTxt = if ($null -ne $f.Start) { "START at {0:F1} s" -f $f.Start } else { "START never delivered" }
            Out-Step ("5 fps      {0}  {1}; {2}; {3}; {4}{5}  {6}.*" -f $tag, (Format-FpsWindow "title" $f.Title), (Format-FpsWindow "menu" $f.Menu), $sTxt, $rtName,
                $(if ($dirty) { "; other load: $($others.Keys -join ', ')" } else { "" }), $base)
            if (-not $ok) { Stop-IfHard $false }
        }
    } else { Out-Step "5 fps      SKIP" }
} catch {
    if ("$_" -notmatch 'verify-merge: hard failure') { Out-Step "ERROR  $_"; $script:failed = $true }
} finally {
    Exit-BuildLock
}
Out-Step ("verify-merge {0} in {1} s  {2}" -f $(if ($script:failed) { "FAIL" } else { "PASS" }), [int]((Get-Date) - $tAll).TotalSeconds, $summaryFile)
if ($script:failed) { exit 1 } else { exit 0 }
