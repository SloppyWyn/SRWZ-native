# Regression check for every runtime merge: run the game, dump the standard set of frames,
# score them against the baseline run.
#     powershell -File tools\regress.ps1 -Name runN [-Seconds 60] [-Baseline local\runs\baseline]
#     powershell -File tools\regress.ps1 -Name runN -NoRun          (score an existing local\runs\runNdump)
#     powershell -File tools\regress.ps1 -MakeBaseline -From local\runs\run33dump   (or -Name runN)
#     powershell -File tools\regress.ps1 -SelfTest
# Standard dump set: every 30 ticks from 1200 to 1920, 2820, then every 600 ticks up to the run
# length (60 ticks/s). The run goes through tools\long-run.ps1 (stall detection, contact sheet
# local\runs\<Name>sheet.png). Scoring is tools\refcompare.py --dirs, by content in order, not by
# tick (boot timing drifts by hundreds of ticks with load, headless/windowed, fast-forward): each
# dump is classified against ALL baseline frames - screen (mad <= -Threshold on a 64x48 grey
# thumbnail and correlation >= 0.8 on 128x96, or a fade of it), blank (uniform; a wildcard, but
# blank for more than -MaxBlank ticks fails), movie (a rich picture standing for the baseline's
# movie frames; with PS2X_MPEG_HASH logs on both sides, the exact baseline position of the same
# picture, and the hashes must agree), anim (an animation caught at another phase) - and the
# matched baseline ticks must not go backwards (repeats and skips allowed). FAIL = no match
# anywhere, or out of order; EDGE = before the baseline's first screen, after its last, or in a
# stretch the baseline never sampled (not counted). -Strict = the old scoring: best frame within
# +-Window ticks of the same tick.
# The baseline keeps only .txt and .rgba (the 4 MB .bin GS memory stays with the run), plus
# mpeghash.txt when the source run logged PS2X_MPEG_HASH lines.
# -FastForward <f> (default 1; 1 or 0 = real time, max = unlimited) and -Exe <runner> go to
# long-run.ps1; -Seconds stays host seconds, so the 600-tick dumps reach f times further into the
# game. -Headless (PS2X_HEADLESS=1, no window) and -Sound (open the audio device; default none,
# PS2X_NO_AUDIO=1) go to long-run.ps1 too.
# Scene checks: after the dump scoring, every checkpoint named in config\game.json "regressScenes"
# (or -Scenes a,b) runs `tools\checkpoint.ps1 scene <name>` (with -Exe if given): a fixed wall stays
# fixed. A scene FAIL fails the regression. -NoScenes skips them; -NoRun skips them too.
param(
    [string]$Name = "",
    [int]$Seconds = 60,
    [string]$Baseline = "",
    [int]$Window = 600,
    [double]$Threshold = 10,
    [int]$MaxBlank = 900,
    [switch]$Strict,
    [string]$FastForward = "1",
    [string]$Exe = "",
    [switch]$Headless,
    [switch]$Sound,
    [switch]$NoRun,
    [string]$Scenes = "",
    [switch]$NoScenes,
    [switch]$MakeBaseline,
    [string]$From = "",
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "gameconfig.ps1"); $root = Get-MainRoot  # main checkout, also from a worktree
. (Join-Path $PSScriptRoot "launch.ps1")  # Invoke-Quiet: children without a console window
$runs = Join-Path $root "local\runs"
if (-not $Baseline) { $Baseline = Join-Path $runs "baseline" }
$refcompare = Join-Path $PSScriptRoot "refcompare.py"  # this checkout's (a worktree tests its own)

function Get-StandardTicks([int]$seconds, [double]$rate = 1.0) {
    $t = @(for ($k = 1200; $k -le 1920; $k += 30) { $k }) + 2820
    for ($k = 3420; $k -le $seconds * 60 * $rate; $k += 600) { $t += $k }
    $t
}

function Compare-Dumps([string]$run, [string]$base, [switch]$strict) {
    $a = @("--dirs", $run, $base, "--threshold", $Threshold, "--max-blank", $MaxBlank)
    if ($strict -or $Strict) { $a += @("--strict", "--window", $Window) }
    $lines = Invoke-Quiet python (@($refcompare) + $a)
    $code = $LASTEXITCODE
    $lines
    return ($code -eq 0)
}

if ($SelfTest) {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $t = Get-StandardTicks 60
    Check ($t.Count -eq 27 -and $t[0] -eq 1200 -and $t[24] -eq 1920 -and $t[25] -eq 2820 -and $t[26] -eq 3420) "standard ticks for 60 s: $($t.Count) ($($t[0])..$($t[24]), $($t[25]), $($t[26]))"
    function Copy-Dumps([string]$from) {
        $d = Join-Path ([IO.Path]::GetTempPath()) ("regress-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
        New-Item -ItemType Directory -Force $d | Out-Null
        Copy-Item (Join-Path $from "gsdump_*.txt"), (Join-Path $from "gsdump_*.rgba") $d
        $d
    }
    function Get-FailTicks($out) { @($out | Where-Object { $_ -match '^FAIL\s+tick' } | ForEach-Object { if ($_ -match 'tick\s+(\d+)') { [int]$Matches[1] } }) }
    $hb1 = Join-Path $runs "hb1dump"; $hb3 = Join-Path $runs "hb3dump"; $r33 = Join-Path $runs "run33dump"
    "-- run33dump vs run32dump (expect PASS, also with -Strict) --"
    $out = Compare-Dumps $r33 (Join-Path $runs "run32dump")
    $out[-2]
    Check $out[-1] "run33 vs run32 passes"
    $out = Compare-Dumps $r33 (Join-Path $runs "run32dump") -strict
    $out[-2]
    Check $out[-1] "run33 vs run32 passes with -Strict"
    "-- hb3dump vs hb1dump (booted ~470 ticks later; expect PASS, and FAIL with -Strict) --"
    $out = Compare-Dumps $hb3 $hb1
    $out | Where-Object { $_ -match '^(EDGE|FAIL)' } | Select-Object -Last 1; $out[-2]
    Check $out[-1] "hb3 vs hb1 passes on content order"
    $out = Compare-Dumps $hb3 $hb1 -strict
    $out[-2]
    Check (-not $out[-1]) "hb3 vs hb1 fails with -Strict (the tick drift it cannot absorb)"
    "-- corrupted copy of run33dump vs run33dump (expect FAIL at 1681) --"
    $tmp = Copy-Dumps $r33
    $f = Join-Path $tmp "gsdump_1681.rgba"
    $b = [IO.File]::ReadAllBytes($f)
    for ($i = 0; $i -lt 640 * 224 * 4; $i += 4) { $b[$i] = 255; $b[$i + 1] = 0; $b[$i + 2] = 255 }  # top half magenta
    [IO.File]::WriteAllBytes($f, $b)
    $out = Compare-Dumps $tmp $r33
    ($out | Where-Object { $_ -match '^FAIL' }); $out[-2]
    Check (-not $out[-1]) "corrupted run fails"
    $fl = Get-FailTicks $out
    Check ($fl.Count -eq 1 -and $fl[0] -eq 1681) "exactly tick 1681 fails"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    "-- hb3dump with the copyright screen at 1801 replaced by the Banpresto logo (expect FAIL at 1801) --"
    $tmp = Copy-Dumps $hb3
    Copy-Item -Force (Join-Path $hb3 "gsdump_1290.rgba") (Join-Path $tmp "gsdump_1801.rgba")
    $out = Compare-Dumps $tmp $hb1
    $fl = Get-FailTicks $out; ($out | Where-Object { $_ -match '^FAIL' }); $out[-2]
    Check (-not $out[-1] -and $fl.Count -eq 1 -and $fl[0] -eq 1801) "wrong screen fails, at 1801 only"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    "-- hb1dump with 1290 (copyright) and 1830 (Namco card) swapped (expect FAIL, out of order) --"
    $tmp = Copy-Dumps $hb1
    Copy-Item -Force (Join-Path $hb1 "gsdump_1830.rgba") (Join-Path $tmp "gsdump_1290.rgba")
    Copy-Item -Force (Join-Path $hb1 "gsdump_1290.rgba") (Join-Path $tmp "gsdump_1830.rgba")
    $out = Compare-Dumps $tmp $hb1
    $fl = Get-FailTicks $out; ($out | Where-Object { $_ -match '^FAIL' }); $out[-2]
    Check (-not $out[-1] -and $fl.Count -ge 1 -and @($fl | Where-Object { $_ -ne 1290 -and $_ -ne 1830 }).Count -eq 0) "swapped screens fail, out of order"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    "-- hb1dump going black from 1740 on (a hang to black; expect FAIL) --"
    $tmp = Copy-Dumps $hb1
    $black = New-Object byte[] (640 * 512 * 4)
    Get-ChildItem $tmp -Filter 'gsdump_*.rgba' | Where-Object { [int]($_.BaseName -replace '\D', '') -ge 1740 } | ForEach-Object { [IO.File]::WriteAllBytes($_.FullName, $black) }
    $out = Compare-Dumps $tmp $hb1
    $out[-2]
    Check (-not $out[-1] -and (Get-FailTicks $out).Count -ge 1) "hang to black fails"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}

if ($MakeBaseline) {
    if (-not $From) {
        if (-not $Name) { throw "-MakeBaseline needs -From <dump dir> or -Name <run>" }
        $From = Join-Path $runs "${Name}dump"
    }
    if (-not (Test-Path (Join-Path $From "gsdump_*.rgba"))) { throw "no gsdump_*.rgba in $From" }
    if (Test-Path $Baseline) { Get-ChildItem $Baseline -Filter 'gsdump_*' | Remove-Item -Force }
    New-Item -ItemType Directory -Force $Baseline | Out-Null
    Copy-Item (Join-Path $From "gsdump_*.txt"), (Join-Path $From "gsdump_*.rgba") $Baseline
    # PS2X_MPEG_HASH lines of the source run (if it logged them) go along: exact movie matching.
    Remove-Item (Join-Path $Baseline "mpeghash.txt") -ErrorAction SilentlyContinue
    $fromFull = (Resolve-Path $From).Path.TrimEnd('\')
    $srcLog = $fromFull -replace 'dump$', '.out.txt'
    if (Test-Path (Join-Path $From "mpeghash.txt")) { Copy-Item (Join-Path $From "mpeghash.txt") $Baseline }
    elseif ($srcLog -ne $fromFull -and (Test-Path $srcLog)) {
        $h = @(Select-String -Path $srcLog -Pattern '^\[MPEG:hash\]' | ForEach-Object { $_.Line })
        if ($h.Count) { Set-Content (Join-Path $Baseline "mpeghash.txt") $h; "baseline: $($h.Count) PS2X_MPEG_HASH lines -> mpeghash.txt" }
    }
    Set-Content (Join-Path $Baseline "SOURCE.txt") "baseline copied from $((Resolve-Path $From).Path) on $(Get-Date -Format 'yyyy-MM-dd HH:mm')"
    "baseline: $(@(Get-ChildItem $Baseline -Filter 'gsdump_*.rgba').Count) frames from $From -> $Baseline"
    exit 0
}

if (-not $Name) { throw "-Name runN is required" }
if (-not (Test-Path (Join-Path $Baseline "gsdump_*.rgba"))) { throw "no baseline in $Baseline (make one with -MakeBaseline)" }
$dumpDir = Join-Path $runs "${Name}dump"
$sheet = Join-Path $runs "${Name}sheet.png"
if (-not $NoRun) {
    if ($FastForward -eq '0') { $FastForward = '1' }  # 0 = off
    $ffRate = if ($FastForward -eq 'max') { 20.0 } else { [double]$FastForward }
    $ticks = (Get-StandardTicks $Seconds $ffRate) -join ','
    $lrArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "tools\long-run.ps1"),
        "-Name", $Name, "-Seconds", $Seconds, "-DumpList", $ticks, "-FastForward", $FastForward)
    if ($Exe) { $lrArgs += @("-Exe", $Exe) }
    if ($Headless) { $lrArgs += "-Headless" }
    if ($Sound) { $lrArgs += "-Sound" }
    Invoke-Quiet powershell $lrArgs -Stream
} elseif (-not (Test-Path $sheet)) {
    $null = Invoke-Quiet python @((Join-Path $root "tools\dumpsheet.py"), $dumpDir, $sheet)
}
"== regression: $dumpDir vs $Baseline =="
$out = Compare-Dumps $dumpDir $Baseline
$pass = $out[-1]
$out[0..($out.Count - 2)]
"contact sheet: $sheet"
if (-not $NoRun -and -not $NoScenes) {
    $sceneList = if ($Scenes) { $Scenes -split ',' } else { @((Get-GameConfig).regressScenes) }
    foreach ($scene in ($sceneList | Where-Object { $_ })) {
        $ckArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "tools\checkpoint.ps1"), "scene", $scene)
        if ($Exe) { $ckArgs += @("-Exe", $Exe) }
        $sceneOut = Invoke-Quiet powershell $ckArgs
        $sceneCode = $LASTEXITCODE
        "scene ${scene}: $(($sceneOut | Select-Object -Last 1))"
        if ($sceneCode -ne 0) { $pass = $false }
    }
}
"REGRESSION $(if ($pass) { 'PASS' } else { 'FAIL' })"
if ($pass) { exit 0 } else { exit 1 }
