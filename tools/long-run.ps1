# Long run with GS dumps and stall detection.
#     powershell -File tools\long-run.ps1 -Name run40 -Seconds 600 [-DumpEvery 600] [-DumpFrom 0] [-StallSeconds 30] [-Trace ...]
#     powershell -File tools\long-run.ps1 -SelfTest
# -DumpList 1200,1230,... replaces the DumpEvery/DumpFrom series (used by tools\regress.ps1).
# -FastForward <f> sets PS2X_FASTFORWARD: the guest clock runs f times faster than wall time
# (1 or 0 = real time, max = unlimited); default 4, the measured safe factor (same screens and
# pictures as 1x, title in ~170 s instead of ~480 s; screens last more ticks). -Seconds stays host
# seconds. -Exe <path> runs another runner build (default: the main third_party\PS2Recomp build).
# No audio device by default (PS2X_NO_AUDIO=1, tools\runstop.ps1 Set-RunnerAudio); -Sound plays sound.
# -Route <name|file|@file|inline steps> sets PS2X_PAD_SCRIPT from tools\route.py (macros/routes in
# config\game.json, -Config for another game; route files config\routes\<name>.txt); without it an
# inherited PS2X_PAD_SCRIPT=@<file> is expanded the same way, any other value is passed unchanged.
# -Headless sets PS2X_HEADLESS=1 (no window; the runner also goes headless by itself when there is
# no monitor). The game is stopped through PS2X_STOP_FILE (tools\runstop.ps1) plus a window close.
# Dumps every DumpEvery VBlank ticks (~60/s) from DumpFrom into local\runs\<Name>dump, runs the
# runner like tools\run-game.ps1 (logs in local\runs\<Name>.out.txt / .err.txt), and watches the
# dump dir and stdout: when neither changes for StallSeconds it records the stall (time, last
# dump tick, last [run:tick] line) and stops the game (stop file + window close, then kill). At the end it
# makes a contact sheet (tools\dumpsheet.py -> local\runs\<Name>sheet.png) and prints a summary.
param(
    [string]$Name = "longrun",
    [int]$Seconds = 300,
    [int]$DumpEvery = 600,
    [int]$DumpFrom = 0,
    [int]$StallSeconds = 30,
    [string]$DumpList = "",
    [string]$Trace = "",
    [string]$FastForward = "4",
    [string]$Exe = "",
    [string]$Route = "",
    [string]$Config = "",
    [switch]$Headless,
    [switch]$Sound,
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "gameconfig.ps1"); $root = Get-MainRoot  # main checkout, also from a worktree
. (Join-Path $PSScriptRoot "runstop.ps1")

function Read-Shared([string]$path) {
    # The runner holds stdout open; read it without locking.
    if (-not (Test-Path $path)) { return "" }
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try { (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Close() }
}

function Get-DumpTicks([string]$dir) {
    @(Get-ChildItem $dir -Filter 'gsdump_*.txt' -ErrorAction SilentlyContinue |
        ForEach-Object { [int]($_.BaseName -replace '^gsdump_', '') } | Sort-Object)
}

function Get-LastTickLine([string]$text) {
    $m = [regex]::Matches($text, '\[run:tick\][^\r\n]*')
    if ($m.Count) { $m[$m.Count - 1].Value } else { "" }
}

# Watches a running process; returns what it saw. Stops it on timeout or stall.
function Watch-Run($proc, [string]$dumpDir, [string]$outPath, [int]$seconds, [int]$stallSeconds, [int]$pollMs = 1000, [string]$stopFile = "") {
    $start = Get-Date
    $lastChange = $start
    $lastSig = ""
    $stall = $null
    $how = ""
    while ($true) {
        if ($proc.HasExited) { $how = "exited by itself, code $($proc.ExitCode)"; break }
        $now = Get-Date
        $files = @(Get-ChildItem $dumpDir -File -ErrorAction SilentlyContinue)
        # Length through an open handle: the directory entry (Get-Item) lags behind a file another
        # process holds open, which made sparse output (a slow title under fast-forward max) look quiet.
        $outLen = 0
        if (Test-Path $outPath) {
            try { $fs = [IO.File]::Open($outPath, 'Open', 'Read', 'ReadWrite'); try { $outLen = $fs.Length } finally { $fs.Close() } }
            catch { $outLen = (Get-Item $outPath).Length }
        }
        $sig = "$($files.Count)/$(($files | Measure-Object Length -Sum).Sum)/$outLen"
        if ($sig -ne $lastSig) { $lastSig = $sig; $lastChange = $now }
        if (($now - $lastChange).TotalSeconds -ge $stallSeconds) {
            $ticks = Get-DumpTicks $dumpDir
            $stall = [pscustomobject]@{
                At        = $now
                AfterSec  = [int]($now - $start).TotalSeconds
                QuietSec  = [int]($now - $lastChange).TotalSeconds
                LastDump  = if ($ticks.Count) { $ticks[-1] } else { $null }
                LastTick  = Get-LastTickLine (Read-Shared $outPath)
            }
            $how = "stalled"
            break
        }
        # With -DumpList, stop once every listed tick has its dump: nothing more will come, and a
        # loaded host (guest VBlank well under 60/s) needs a generous -Seconds to get there.
        if ($script:DumpList -and -not $script:SelfTest) {
            $want = @($script:DumpList -split ',' | Where-Object { $_ }).Count
            $new = @($files | Where-Object { $_.Name -like 'gsdump_*.txt' -and $_.LastWriteTime -ge $start }).Count
            if ($new -ge $want) { Start-Sleep -Seconds 2; $how = "all $want dumps taken"; break }
        }
        if (($now - $start).TotalSeconds -ge $seconds) { $how = "time up"; break }
        Start-Sleep -Milliseconds $pollMs
    }
    if (-not $proc.HasExited) { $how += ", " + (Stop-Runner $proc $stopFile) }
    elseif ($stopFile) { Remove-Item -LiteralPath $stopFile -Force -ErrorAction SilentlyContinue }
    [pscustomobject]@{ How = $how; Stall = $stall; Elapsed = [int]((Get-Date) - $start).TotalSeconds }
}

function Write-Summary($result, [string]$dumpDir, [string]$outPath, [string]$sheet) {
    $ticks = Get-DumpTicks $dumpDir
    $last = Get-LastTickLine (Read-Shared $outPath)
    $lastTick = if ($last -match 'tick=(\d+)') { $Matches[1] } else { "none" }
    if ($ticks.Count) {
        $null = Invoke-Quiet python @((Join-Path $root "tools\dumpsheet.py"), $dumpDir, $sheet)
        $sheetText = if (Test-Path $sheet) { $sheet } else { "(dumpsheet failed)" }
    } else { $sheetText = "(no dumps, no sheet)" }
    "== summary =="
    "run:        $($result.How) after $($result.Elapsed) s"
    "last tick:  $lastTick   ($last)"
    if ($result.Stall) {
        $s = $result.Stall
        "stall:      YES at $($s.At.ToString('HH:mm:ss')) ($($s.AfterSec) s in, quiet $($s.QuietSec) s); last dump tick $($s.LastDump)"
        "            last [run:tick]: $($s.LastTick)"
    } else { "stall:      no" }
    "dumps:      $($ticks.Count)$(if ($ticks.Count) { " (ticks $($ticks[0]) .. $($ticks[-1]))" })"
    "sheet:      $sheetText"
}

if ($SelfTest) {
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("longrun-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $dumpDir = Join-Path $tmp "fakedump"
    New-Item -ItemType Directory -Force $dumpDir | Out-Null
    $out = Join-Path $tmp "fake.out.txt"; $err = Join-Path $tmp "fake.err.txt"
    # Dummy "game": writes 3 dumps (real format, black frame) and tick lines, then goes quiet.
    $fake = Join-Path $tmp "fake.ps1"
    @'
param($dir)
$rgba = New-Object byte[] (640*512*4)
foreach ($t in 600,1200,1800) {
    [IO.File]::WriteAllBytes("$dir\gsdump_$t.rgba", $rgba)
    Set-Content "$dir\gsdump_$t.txt" "tick=$t`npresented=640x448 rowstride=640"
    [Console]::Out.WriteLine("[run:tick] tick=$t pc=0x100000 vsync=$t"); [Console]::Out.Flush()
    Start-Sleep -Milliseconds 700
}
Start-Sleep -Seconds 120
'@ | Set-Content $fake -Encoding utf8
    $ps = (Get-Command powershell.exe).Source
    $p = Start-Quiet $ps -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $fake, $dumpDir `
        -StdOut $out -StdErr $err
    $r = Watch-Run $p $dumpDir $out 60 4 250
    $sheet = Join-Path $tmp "fakesheet.png"
    $summary = Write-Summary $r $dumpDir $out $sheet
    $summary
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    Check ($null -ne $r.Stall) "stall detected"
    Check ($r.Stall -and $r.Stall.LastDump -eq 1800) "stall records last dump tick 1800"
    Check ($r.Stall -and $r.Stall.LastTick -match 'tick=1800') "stall records last [run:tick] line"
    Check ($r.Elapsed -lt 30) "stopped well before the 60 s limit ($($r.Elapsed) s)"
    Check ($p.HasExited) "dummy process stopped"
    Check (Test-Path $sheet) "contact sheet written"
    Check (($summary -join "`n") -match 'dumps:\s+3') "summary counts 3 dumps"
    # And a quiet-free run must not report a stall: same watcher, short limit.
    $p2 = Start-Quiet $ps -ArgumentList "-NoProfile", "-Command", "Start-Sleep 30" `
        -StdOut (Join-Path $tmp "p2.out.txt") -StdErr (Join-Path $tmp "p2.err.txt")
    $r2 = Watch-Run $p2 $dumpDir (Join-Path $tmp "p2.out.txt") 3 10 250
    Check ($null -eq $r2.Stall -and $r2.How -like "time up*") "time limit without stall: '$($r2.How)'"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}

$exe = if ($Exe) { (Resolve-Path $Exe).Path } else { Join-Path $root "third_party\PS2Recomp\out\build\ps2xRuntime\Release\ps2EntryRunner.exe" }
# Guest ticks per host second scale with the fast-forward factor; "max" goes as fast as the host
# allows, so plan for 20x.
if ($FastForward -eq '0') { $FastForward = '1' }  # 0 = off, like PS2X_FASTFORWARD=0
$ffRate = if ($FastForward -eq 'max') { 20.0 } else { [double]$FastForward }
if ($ffRate -lt 1) { throw "-FastForward must be >= 1, 0 (off) or max (got '$FastForward')" }
$runs = Join-Path $root "local\runs"
$dumpDir = Join-Path $runs "${Name}dump"
New-Item -ItemType Directory -Force $dumpDir | Out-Null
$dumpDir = (Resolve-Path $dumpDir).Path
$out = Join-Path $runs "$Name.out.txt"
$err = Join-Path $runs "$Name.err.txt"
# Dump ticks: enough for the whole run at ~60 ticks/s (times the fast-forward rate) with margin,
# capped at 2500 dumps to stay inside the environment-variable size limit.
$maxTick = $DumpFrom + [int][Math]::Min($Seconds * 90 * $ffRate, 2500.0 * $DumpEvery) + $DumpEvery
$list = for ($t = $DumpFrom; $t -le $maxTick; $t += $DumpEvery) { $t }
$env:PS2X_GS_DUMP = if ($DumpList) { $DumpList } else { $list -join ',' }
$env:PS2X_GS_DUMP_DIR = $dumpDir
# Silent, no audio device, unless -Sound or the caller set PS2X_MUTE=0 (Wyn, 2026-10-06).
Set-RunnerAudio ([bool]$Sound)
if ($Trace) { $env:PS2X_TRACE_STUBS = $Trace } else { Remove-Item Env:PS2X_TRACE_STUBS -ErrorAction SilentlyContinue }
if ($FastForward -eq '1') { Remove-Item Env:PS2X_FASTFORWARD -ErrorAction SilentlyContinue } else { $env:PS2X_FASTFORWARD = $FastForward }
if ($Headless) { Set-RunnerHeadless }
if ($Route) { $env:PS2X_PAD_SCRIPT = Get-RoutePadScript $Route $Config }
elseif ($env:PS2X_PAD_SCRIPT) { $env:PS2X_PAD_SCRIPT = Expand-PadScriptValue $env:PS2X_PAD_SCRIPT $Config }
if ($env:PS2X_PAD_SCRIPT) { "pad script$(if ($Route) { " (route $Route)" }): $env:PS2X_PAD_SCRIPT" }
$stop = New-RunnerStopFile (Join-Path $runs $Name)
"fast-forward: $FastForward; runner: $exe$(if ($env:PS2X_HEADLESS -and $env:PS2X_HEADLESS -ne '0') { ' (headless)' })"
"dumping $(if ($DumpList) { "at the given ticks" } else { "every $DumpEvery ticks from $DumpFrom" }) into $dumpDir; up to $Seconds s, stall after $StallSeconds s quiet"
# tools\launch.ps1 (via runstop.ps1): no console window; the game window opens behind Wyn's
# window (GLFW activates it on show: the focus guard hands the foreground back).
$p = Start-Quiet $exe -ArgumentList "$root\local\game\SLPS_258.87", "$root\disc\srwz.iso" `
    -WorkingDirectory (Join-Path $root "local\game") -StdOut $out -StdErr $err -FocusGuardSeconds $(if ($Headless) { 0 } else { 60 })
$r = Watch-Run $p $dumpDir $out $Seconds $StallSeconds 1000 $stop
if (Test-RunnerHeadless $out) { "runner ran headless (no window)" }
"stdout: $((Get-Content $out).Count) lines, stderr: $((Get-Content $err).Count) lines -> $runs\$Name.*.txt"
Write-Summary $r $dumpDir $out (Join-Path $runs "${Name}sheet.png")
