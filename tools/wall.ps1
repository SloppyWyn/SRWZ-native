# One command from a wall investigation to a report: (re)build, run to a named checkpoint with GS
# dumps around it, contact sheet, PCSX2 reference comparison, regression score, log summary, all
# in local\runs\<Name>\report.md (+ sheet.png, side.png), with a 10-line summary on the console.
#     powershell -File tools\wall.ps1 -Name runN -Checkpoint logo [-Build] [-Recomp] [-Compare]
#                [-Seconds N] [-Env K=V,K2=V2] [-FastForward f|max] [-Headless] [-Sound] [-DryRun] [-Force]
#     powershell -File tools\wall.ps1 -NoRun -From local\runs\run43dump [-Checkpoint logo] [-Compare] [-Name x]
#     powershell -File tools\wall.ps1 -List          (checkpoints in the game config)
#     powershell -File tools\wall.ps1 -SelfTest [-From <dump dir>]
# Everything game-specific comes from config\game.json (-Config <file> or $env:PS2X_GAME_CONFIG
# for another game): ELF/disc/runner paths, build steps, checkpoints (vsync tick, dump window,
# PCSX2 reference image, optional snapshot). Paths resolve to the MAIN checkout's local\, also
# when this runs from a worktree.
#   -Recomp     runs the config's "recomp" build steps (delete recompiler output, ps2_recomp,
#               overlays.py); -Build the "build" steps (tools\build-game.bat, which holds the
#               shared build lock). -DryRun prints the build steps and the run plan, runs nothing.
#   run         tools\long-run.ps1 with -DumpList = the checkpoint's dump window, with no audio
#               device (PS2X_NO_AUDIO=1; -Sound opens it with sound on), for
#               until the last dump is in or the time limit (-Seconds; default: time to the
#               window's end at 60 ticks/s + boot + tail, times runner.timeLimitFactor).
#               Fast-forward (PS2X_FASTFORWARD) and snapshot start (PS2X_SNAPSHOT_LOAD, the
#               checkpoint's snapshot file) are used only when the runner supports them: the
#               env var's name is in the runner exe's strings (cached per exe build), or the
#               config's runner.features.<f>.support is true/false. Without fast-forward a run
#               longer than runner.maxRealtimeSeconds is refused unless -Force.
#   -Compare    also scores the dumps against the regression baseline (tools\regress.ps1 -NoRun);
#               the reference comparison (tools\refcompare.py) runs whenever the checkpoint has one.
#   -Headless   no window (PS2X_HEADLESS=1, via long-run.ps1); the runner also goes headless by
#               itself when there is no monitor. Stops go through PS2X_STOP_FILE (tools\runstop.ps1).
#   -Sound      play sound (default: no audio device at all, so no device switching per run).
#   -Env        extra environment for the runner, K=V pairs split at commas (a piece without '='
#               continues the previous value: PS2X_TRACE_STUBS=Sif,Cd works; it goes to -Trace).
#   -Route      pad input from a route (tools\route.py: a name from the config's pad.routes/macros
#               or config\routes\<name>.txt, a route file, @file or inline steps) as PS2X_PAD_SCRIPT.
#               A checkpoint may name one ("route": "select_scenario"); -Route overrides it, -Env
#               PS2X_PAD_SCRIPT=... overrides the checkpoint's, and PS2X_PAD_SCRIPT=@<file> is expanded.
param(
    [string]$Name = "",
    [string]$Checkpoint = "",
    [switch]$Build,
    [switch]$Recomp,
    [switch]$Compare,
    [int]$Seconds = 0,
    [string]$Env = "",
    [string]$Route = "",
    [string]$FastForward = "",
    [switch]$Headless,
    [switch]$Sound,
    [int]$StallSeconds = 30,
    [switch]$NoRun,
    [string]$From = "",
    [string]$Logs = "",
    [switch]$DryRun,
    [switch]$Force,
    [switch]$List,
    [string]$Config = "",
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "gameconfig.ps1")
. (Join-Path $PSScriptRoot "launch.ps1")  # Invoke-Quiet: children without a console window
. (Join-Path $PSScriptRoot "missingtargets.ps1")  # MISSING FUNCTION lines ([guest-branch:missing-target])

# ---------------------------------------------------------------- config helpers

function Get-Checkpoint($cfg, [string]$name) {
    $p = $cfg.checkpoints.PSObject.Properties[$name]
    if (-not $p) { throw "unknown checkpoint '$name' (config has: $(($cfg.checkpoints.PSObject.Properties.Name) -join ', '))" }
    $cp = $p.Value
    if ($null -eq $cp.tick) { throw "checkpoint '$name': no tick" }
    if (-not $cp.dump) { throw "checkpoint '$name': no dump window" }
    $cp
}

# Dump ticks of a checkpoint: dump.ticks (explicit list) or from..to step every.
function Get-DumpTicks($cp) {
    if ($cp.dump.ticks) { return @($cp.dump.ticks | ForEach-Object { [int]$_ }) }
    $from = [int]$cp.dump.from; $to = [int]$cp.dump.to; $every = [int]$cp.dump.every
    if ($every -le 0 -or $to -lt $from) { throw "bad dump window from=$from to=$to every=$every" }
    @(for ($t = $from; $t -le $to; $t += $every) { $t })
}

function Get-Setting($obj, [string]$name, $default) {
    if ($obj -and $obj.PSObject.Properties[$name] -and $null -ne $obj.$name) { $obj.$name } else { $default }
}

# "A=1,B=x,y,C=2" -> ordered @{A=1; B='x,y'; C=2}
function ConvertFrom-EnvList([string]$s) {
    $h = [ordered]@{}
    $last = $null
    foreach ($piece in ($s -split ',')) {
        if ($piece -match '^\s*([A-Za-z_][A-Za-z0-9_]*)=(.*)$') { $last = $Matches[1]; $h[$last] = $Matches[2] }
        elseif ($last) { $h[$last] = $h[$last] + ',' + $piece }
        elseif ($piece.Trim()) { throw "-Env: '$piece' is not K=V" }
    }
    $h
}

# Expands {root} and {pathkey} placeholders of a build step argument.
function Expand-Arg($cfg, [string]$a) {
    $r = [regex]::Replace($a, '\{([A-Za-z]+)\}', {
            param($m)
            $k = $m.Groups[1].Value
            if ($k -eq 'root') { return $cfg._root }
            $v = Resolve-GamePath $cfg $k
            if ($null -eq $v) { throw "build step: unknown placeholder {$k}" }
            $v })
    if ($a -match '^\{') { $r = $r -replace '/', '\' }
    $r
}

function Get-BuildSteps($cfg, [bool]$recomp, [bool]$build) {
    $steps = @()
    $all = if ($cfg.build) { @($cfg.build.steps) } else { @() }
    foreach ($s in $all) {
        if (($s.stage -eq 'recomp' -and $recomp) -or ($s.stage -eq 'build' -and $build)) {
            $steps += [pscustomobject]@{
                Name  = $s.name
                Clean = if ($s.clean) { Expand-Arg $cfg $s.clean } else { $null }
                Cmd   = @($s.cmd | ForEach-Object { Expand-Arg $cfg $_ })
            }
        }
    }
    $steps
}

function Invoke-BuildSteps($steps, [bool]$dry) {
    foreach ($s in $steps) {
        $line = ($s.Cmd | ForEach-Object { if ($_ -match '\s') { "`"$_`"" } else { $_ } }) -join ' '
        if ($dry) {
            if ($s.Clean) { "[dry-run] $($s.Name): delete $($s.Clean)" }
            "[dry-run] $($s.Name): $line"
            continue
        }
        if ($s.Clean -and (Test-Path $s.Clean)) { "== $($s.Name): deleting $($s.Clean)"; Remove-Item -Recurse -Force $s.Clean }
        "== $($s.Name): $line"
        $t0 = Get-Date
        $exe = $s.Cmd[0]; $rest = @($s.Cmd | Select-Object -Skip 1)
        $null = Invoke-Quiet $exe $rest -MergeErr -Echo
        if ($LASTEXITCODE -ne 0) { throw "build step $($s.Name) failed (exit $LASTEXITCODE)" }
        "   $($s.Name) done in $([int]((Get-Date) - $t0).TotalSeconds) s"
    }
}

# ---------------------------------------------------------------- runner features

# Which of the given env var names occur in the exe's bytes. Cached per exe (path, size, mtime).
function Find-ExeStrings([string]$exe, [string[]]$names, [string]$cacheFile) {
    $res = @{}
    if (-not (Test-Path $exe)) { foreach ($n in $names) { $res[$n] = $false }; return $res }
    $fi = Get-Item $exe
    $key = "$($fi.FullName)|$($fi.Length)|$($fi.LastWriteTimeUtc.Ticks)"
    $cache = @{}
    if ($cacheFile -and (Test-Path $cacheFile)) {
        try { (Get-Content -Raw $cacheFile | ConvertFrom-Json).PSObject.Properties | ForEach-Object { $cache[$_.Name] = $_.Value } } catch { }
    }
    $missing = @($names | Where-Object { -not ($cache[$key] -and $cache[$key].PSObject.Properties[$_]) })
    if ($missing.Count) {
        $text = [Text.Encoding]::GetEncoding(28591).GetString([IO.File]::ReadAllBytes($exe))
        $entry = [ordered]@{}
        if ($cache[$key]) { $cache[$key].PSObject.Properties | ForEach-Object { $entry[$_.Name] = $_.Value } }
        foreach ($n in $missing) { $entry[$n] = $text.Contains($n) }
        $text = $null
        $cache = @{ $key = [pscustomobject]$entry }   # keep only the current build
        if ($cacheFile) { try { $cache | ConvertTo-Json -Depth 4 | Set-Content $cacheFile -Encoding utf8 } catch { } }
    }
    foreach ($n in $names) { $res[$n] = [bool]$cache[$key].$n }
    $res
}

# @{ fastforward = @{ Env; Supported; How; ... }; snapshot = ... }
function Get-RunnerFeatures($cfg, [string]$exe, [string]$cacheFile) {
    $feats = @{}
    $fcfg = Get-Setting $cfg.runner 'features' $null
    if (-not $fcfg) { return $feats }
    $auto = @()
    foreach ($p in $fcfg.PSObject.Properties) { if ("$($p.Value.support)" -eq 'auto') { $auto += $p.Value.env } }
    $found = if ($auto.Count) { Find-ExeStrings $exe $auto $cacheFile } else { @{} }
    foreach ($p in $fcfg.PSObject.Properties) {
        $f = $p.Value; $sup = "$($f.support)"
        $ok = if ($sup -eq 'auto') { [bool]$found[$f.env] } else { $sup -eq 'True' -or $sup -eq 'true' }
        $how = if ($sup -eq 'auto') { if ($ok) { "found in runner" } else { "not in runner" } } else { "config says $sup" }
        $feats[$p.Name] = [pscustomobject]@{ Env = $f.env; Supported = $ok; How = $how; Cfg = $f }
    }
    $feats
}

# Plan of a run: start tick, rate, seconds, env.
function Get-RunPlan($cfg, $cp, $feats, [int]$seconds, [string]$ff, [bool]$force) {
    $tps = [double](Get-Setting $cfg.runner 'ticksPerSecond' 60)
    $boot = [int](Get-Setting $cfg.runner 'bootSeconds' 10)
    $tail = [int](Get-Setting $cfg.runner 'tailSeconds' 5)
    $maxRt = [int](Get-Setting $cfg.runner 'maxRealtimeSeconds' 120)
    # A checkpoint may pin its fast-forward factor ("fastForward": "1" for screens that break under
    # fast-forward); -FastForward still overrides it, and a pinned factor is never refused as too long.
    $cpFf = "$(Get-Setting $cp 'fastForward' '')"
    if (-not $ff -and $cpFf) { $ff = $cpFf }
    $ticks = Get-DumpTicks $cp
    $penv = [ordered]@{}
    $notes = @()
    $start = 0
    $snap = $feats['snapshot']
    $sc = Get-Setting $cp 'snapshot' $null
    if ($sc) {
        $dir = Resolve-GamePath $cfg 'snapshots'
        $file = if ([IO.Path]::IsPathRooted($sc.file)) { $sc.file } elseif ($dir) { Join-Path $dir $sc.file } else { $sc.file }
        if (-not $snap -or -not $snap.Supported) { $notes += "snapshot: runner lacks $(if ($snap) { $snap.Env } else { 'snapshot support' }); running from boot" }
        elseif (-not (Test-Path $file)) { $notes += "snapshot: $file missing; running from boot" }
        else { $penv[$snap.Env] = $file; $start = [int]$sc.tick; $notes += "snapshot: starting from $file (tick $start)" }
    }
    $rate = 1.0
    $f = $feats['fastforward']
    if ($ff -eq '1') { $notes += "fast-forward: off (-FastForward 1)" }
    elseif ($f -and $f.Supported) {
        $val = if ($ff) { $ff } else { "$(Get-Setting $f.Cfg 'value' 'max')" }
        $penv[$f.Env] = $val
        $rate = if ($val -eq 'max' -or $val -eq '0') { [double](Get-Setting $f.Cfg 'assumedRate' 4) } else { [double]$val }
        $notes += "fast-forward: $($f.Env)=$val (planning at ${rate}x)"
        # Unlimited mode charges busy host time at PS2X_FF_BUSY_SCALE x wall time, so host-bound
        # phases (movie decode, loads) cost extra VBlanks: SRWZ's title came at VBlank 32k-46k
        # instead of ~27k, so a VBlank dump window can end before the checkpoint shows. A fixed
        # factor (-FastForward 4) keeps VBlank ticks in step with the content.
        if ($val -eq 'max' -and -not $start) { $notes += "WARNING: at max, VBlank ticks run ahead of the content (x1.2-1.7 seen); this window may miss the checkpoint: prefer a fixed factor" }
    } else { $notes += "fast-forward: not available ($(if ($f) { $f.How } else { 'no feature in config' })); real time" }
    $need = [int][Math]::Ceiling(([Math]::Max(0, $ticks[-1] - $start)) / $tps / $rate) + $tail + $(if ($start) { 0 } else { $boot })
    # The run ends when the last dump is in (long-run.ps1 with -DumpList); the time limit leaves
    # room for a loaded host, where the guest clock runs well under 60 VBlanks/s.
    $factor = [double](Get-Setting $cfg.runner 'timeLimitFactor' 3)
    $secs = if ($seconds -gt 0) { $seconds } else { [int][Math]::Ceiling($need * $factor) }
    if ($seconds -gt 0) { $need = $seconds }
    $notes += "expect ~$need s; time limit $secs s; stops once all $($ticks.Count) dumps are in"
    $refused = $null
    if ($rate -le 1.0 -and $need -gt $maxRt -and -not $force -and -not $cpFf) {
        $refused = "run would take $need s at real time (> runner.maxRealtimeSeconds $maxRt) and the runner has no fast-forward; use -Force, a snapshot or a shorter checkpoint"
    }
    [pscustomobject]@{ Ticks = $ticks; Start = $start; Rate = $rate; Seconds = $secs; Need = $need; Env = $penv; Notes = $notes; Refused = $refused }
}

# ---------------------------------------------------------------- log + dump analysis

function Read-Lines([string]$path) {
    if (-not (Test-Path $path)) { return @() }
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try { $t = (New-Object IO.StreamReader($fs)).ReadToEnd() } finally { $fs.Close() }
    @($t -split "`r?`n")
}

function Get-LogStats([string]$logBase) {
    $out = Read-Lines "$logBase.out.txt"; $err = Read-Lines "$logBase.err.txt"
    $ticks = @()
    foreach ($l in $out) {
        if ($l -match '\[run:tick\].*hostMs=(\d+) eeMs=(\d+) vsync=(\d+) dma=(\d+) gif=(\d+)') {
            $ticks += [pscustomobject]@{ Host = [long]$Matches[1]; Ee = [long]$Matches[2]; Vs = [long]$Matches[3]; Dma = [long]$Matches[4]; Gif = [long]$Matches[5] }
        }
    }
    $s = [ordered]@{ OutLines = $out.Count; ErrLines = $err.Count; TickLines = $ticks.Count }
    if ($ticks.Count -ge 2) {
        $a = $ticks[0]; $b = $ticks[-1]; $dt = ($b.Host - $a.Host) / 1000.0
        $s.LastVsync = $b.Vs; $s.HostSec = [Math]::Round($b.Host / 1000.0, 1); $s.DriftMs = $b.Host - $b.Ee
        $s.VsyncPerSec = [Math]::Round(($b.Vs - $a.Vs) / $dt, 1)
        $s.DmaPerSec = [Math]::Round(($b.Dma - $a.Dma) / $dt, 1); $s.GifPerSec = [Math]::Round(($b.Gif - $a.Gif) / $dt, 1)
    } elseif ($ticks.Count -eq 1) { $s.LastVsync = $ticks[0].Vs; $s.HostSec = [Math]::Round($ticks[0].Host / 1000.0, 1); $s.DriftMs = $ticks[0].Host - $ticks[0].Ee }
    $s.Errors = @($err | Where-Object { $_ -match "error|exception|abort|fatal" -and $_ -notmatch "sceCdGetError|GetError" }).Count
    $s.PresentNone = @($err | Where-Object { $_ -match "\[gs:present:none\]" }).Count
    $s.Watchdog = @($err | Where-Object { $_ -match "^\[watchdog\] STALL report" })
    $s.Missing = @(Get-MissingTargets $err)
    $s.Categories = @($err | Where-Object { $_ } | ForEach-Object { if ($_ -match "^(\[[^\]]*\])") { $Matches[1] -replace "[0-9a-fx]{4,}", "" } else { "(other)" } } |
        Group-Object | Sort-Object Count -Descending | Select-Object -First 5 | ForEach-Object { "$($_.Name) $($_.Count)" })
    [pscustomobject]$s
}

function Get-DumpRows([string]$dumpDir) {
    Get-ChildItem $dumpDir -Filter 'gsdump_*.txt' -ErrorAction SilentlyContinue |
        Sort-Object { [int]($_.BaseName -replace '^gsdump_', '') } | ForEach-Object {
            $kv = @{}
            foreach ($l in (Get-Content $_.FullName)) {
                if ($l -match '^(\w+)=(.*)$') { $kv[$Matches[1]] = $Matches[2] }
            }
            [pscustomobject]@{
                Tick = [int]($_.BaseName -replace '^gsdump_', '')
                Presented = if ($kv['presented'] -match '^0x0') { 'none' } elseif ($kv['presented'] -match '^(\S+)') { $Matches[1] } else { '?' }
                Pmode = $kv['pmode']; Smode2 = $kv['smode2']; Dispfb1 = $kv['dispfb1']; Display1 = $kv['display1']
            }
        }
}

# ---------------------------------------------------------------- report

function New-WallReport {
    param($cfg, [string]$cpName, $cp, [string]$name, [string]$dumpDir, [string]$logBase, [string]$reportDir,
          [bool]$doCompare, $runInfo)
    New-Item -ItemType Directory -Force $reportDir | Out-Null
    $tools = $PSScriptRoot
    $md = New-Object Collections.Generic.List[string]
    $bt = [char]96
    $rows = @(Get-DumpRows $dumpDir)
    $sheet = Join-Path $reportDir "sheet.png"
    $side = Join-Path $reportDir "side.png"
    if ($rows.Count) { $null = Invoke-Quiet python @((Join-Path $tools "dumpsheet.py"), $dumpDir, $sheet) }

    # Reference
    $refLine = "reference: none for this checkpoint"
    $refOut = @()
    $refFile = $null
    if ($cp -and $cp.reference) {
        $refFile = Join-Path (Resolve-GamePath $cfg 'reference') ($cp.reference -replace '/', '\')
        if (-not (Test-Path $refFile)) { $refLine = "reference: $($cp.reference) MISSING" }
        elseif (-not $rows.Count) { $refLine = "reference: no dumps to compare" }
        else {
            $refOut = @(Invoke-Quiet python @((Join-Path $tools "refcompare.py"), $refFile, $dumpDir, "-o", $side, "--top", "5"))
            $best = $refOut | Where-Object { $_ -match '^\s+tick\s+(\d+)\s+([\d.]+)' } | Select-Object -First 1
            $verdict = ($refOut | Where-Object { $_ -match '^verdict:' }) -replace '^verdict:\s*', ''
            if ($best -match 'tick\s+(\d+)\s+([\d.]+)') {
                $refLine = "reference: $($cp.reference) best tick $($Matches[1]) mad $($Matches[2]) ($verdict)"
            } else { $refLine = "reference: $($cp.reference) compare failed" }
        }
    }

    # Regression
    $regLine = "regression: not scored (add -Compare)"
    $regOut = @()
    if ($doCompare) {
        # A checkpoint may have its own baseline ("baseline": a dump folder, relative to the main
        # checkout), e.g. one made from a route run; otherwise paths.baseline.
        $cpBase = Get-Setting $cp 'baseline' $null
        $base = if ($cpBase) { $v = $cpBase -replace '/', '\';if ([IO.Path]::IsPathRooted($v)) { $v } else { Join-Path $cfg._root $v } } else { Resolve-GamePath $cfg 'baseline' }
        $runs = Resolve-GamePath $cfg 'runs'
        if (-not $base -or -not (Test-Path (Join-Path $base 'gsdump_*.rgba'))) { $regLine = "regression: no baseline at $base" }
        elseif (-not $rows.Count) { $regLine = "regression: no dumps" }
        else {
            $leaf = Split-Path -Leaf $dumpDir
            $std = ($leaf -match '^(.+)dump$') -and ((Join-Path $runs $leaf) -eq ((Resolve-Path $dumpDir).Path.TrimEnd('\')))
            if ($std) {
                $regOut = @(Invoke-Quiet powershell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $tools "regress.ps1"), "-Name", ($leaf -replace 'dump$', ''), "-NoRun", "-Baseline", $base))
            } else {
                $regOut = @(Invoke-Quiet python @((Join-Path $tools "refcompare.py"), "--dirs", $dumpDir, $base))
            }
            $v = $regOut | Where-Object { $_ -match '^verdict:' } | Select-Object -Last 1
            $regLine = if ($v) { "regression: " + ($v -replace '^verdict:\s*', '') } else { "regression: scoring failed" }
            $fails = @($regOut | Where-Object { $_ -match '^FAIL\s+tick' } | ForEach-Object { if ($_ -match 'tick\s+(\d+)') { $Matches[1] } })
            if ($fails.Count) { $regLine += "; FAIL ticks " + (($fails | Select-Object -First 6) -join ',') }
        }
    }

    # Log
    $haveLogs = (Test-Path "$logBase.out.txt") -or (Test-Path "$logBase.err.txt")
    $st = if ($haveLogs) { Get-LogStats $logBase } else { $null }
    $rr = @()
    if ($haveLogs) {
        $rr = @(Invoke-Quiet powershell @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $tools "run-report.ps1"), "-Name", $name, "-NoRun", "-LogBase", $logBase))
    }

    # 10-line summary
    $tickRange = if ($rows.Count) { "$($rows.Count) dumps, ticks $($rows[0].Tick)..$($rows[-1].Tick)" } else { "no dumps" }
    $sizes = ($rows | Group-Object Presented | ForEach-Object { "$($_.Name) x$($_.Count)" }) -join ', '
    $sum = @(
        "wall $name @ $(if ($cpName) { "$cpName (tick $($cp.tick))" } else { '(no checkpoint)' }): $(if ($runInfo) { $runInfo.How } else { 're-report of existing dumps' })"
        $(if ($st -and $null -ne $st.LastVsync) { "clock: vsync $($st.LastVsync) at host $($st.HostSec) s, drift $($st.DriftMs) ms, VBlank $($st.VsyncPerSec)/s$(if ($null -ne $st.VsyncPerSec -and $st.VsyncPerSec -lt 50) { ' (SLOW: host loaded or guest stuck)' })" } else { "clock: no [run:tick] lines" })
        $(if ($st -and $null -ne $st.DmaPerSec) { "rates: DMA $($st.DmaPerSec)/s, GIF $($st.GifPerSec)/s ($($st.TickLines) tick lines)" } else { "rates: n/a" })
        "dumps: $tickRange$(if ($sizes) { "; presented $sizes" })"
        $refLine
        $regLine
        $(if ($st) { "trouble: $(if ($st.Missing.Count) { "$($st.Missing.Count) MISSING FUNCTION (listed below), " })$($st.Errors) error lines, $($st.PresentNone) [gs:present:none], $($st.Watchdog.Count) watchdog STALL reports" } else { "trouble: no logs" })
        $(if ($st) { "stderr top: " + ($st.Categories -join '; ') } else { "stderr: no logs" })
        $(if ($runInfo) { ($runInfo.Notes -join '; ') } else { "run: none (-NoRun)" })
        "report: $(Join-Path $reportDir 'report.md')"
    )

    $md.Add("# Wall report: $name$(if ($cpName) { " at checkpoint $($bt)$cpName$($bt)" })")
    $md.Add("")
    $md.Add("$($cfg.name) ($($cfg.serial)), $(Get-Date -Format 'yyyy-MM-dd HH:mm'). Dumps: $($bt)$dumpDir$($bt); logs: $($bt)$logBase.out.txt$($bt) / $($bt).err.txt$($bt).")
    if ($cp -and $cp.notes) { $md.Add(""); $md.Add("Checkpoint: $($cp.notes)") }
    $md.Add("")
    $md.Add("## Summary")
    $md.Add("")
    $md.Add('```')
    $sum | ForEach-Object { $md.Add($_) }
    $md.Add('```')
    $md.Add("")
    $script:WallMissing = if ($st) { $st.Missing } else { @() }
    if ($script:WallMissing.Count) {
        $md.Add("## MISSING FUNCTION")
        $md.Add("")
        $md.Add("The runtime parked a thread at a guest address with no recompiled function. Fix: $MissingTargetHint.")
        $md.Add("")
        $md.Add('```'); $script:WallMissing | ForEach-Object { $md.Add((Format-MissingTarget $_)) }; $md.Add('```'); $md.Add("")
    }
    $md.Add("## Contact sheet")
    $md.Add("")
    if (Test-Path $sheet) { $md.Add("![contact sheet](sheet.png)") } else { $md.Add("(no dumps)") }
    $md.Add("")
    $md.Add("## Reference (PCSX2)")
    $md.Add("")
    $md.Add($refLine)
    if (Test-Path $side) { $md.Add(""); $md.Add("![reference vs best dump](side.png)") }
    if ($refOut.Count) { $md.Add(""); $md.Add('```'); $refOut | ForEach-Object { $md.Add($_) }; $md.Add('```') }
    $md.Add("")
    $md.Add("## Regression vs baseline")
    $md.Add("")
    if ($doCompare -and $base) { $md.Add("Baseline: $base"); $md.Add("") }
    $md.Add($regLine)
    if ($regOut.Count) { $md.Add(""); $md.Add('```'); $regOut | ForEach-Object { $md.Add($_) }; $md.Add('```') }
    $md.Add("")
    $md.Add("## Dumps")
    $md.Add("")
    if ($rows.Count) {
        $md.Add("| tick | presented | PMODE | SMODE2 | DISPFB1 | DISPLAY1 |")
        $md.Add("|---:|---|---|---|---|---|")
        foreach ($r in $rows) { $md.Add("| $($r.Tick) | $($r.Presented) | $($r.Pmode) | $($r.Smode2) | $($r.Dispfb1) | $($r.Display1) |") }
    } else { $md.Add("(none)") }
    $md.Add("")
    $md.Add("## Log summary (tools\run-report.ps1)")
    $md.Add("")
    if ($st -and $st.Watchdog.Count) {
        $md.Add("Watchdog stall reports (one ~6 s after boot is the boot-time load, not a hang):")
        $md.Add("")
        $md.Add('```'); $st.Watchdog | Select-Object -First 10 | ForEach-Object { $md.Add($_) }; $md.Add('```'); $md.Add("")
    }
    if ($rr.Count) { $md.Add('```'); $rr | ForEach-Object { $md.Add($_) }; $md.Add('```') } else { $md.Add("(no logs)") }
    $runTxt = Join-Path $reportDir "run.txt"
    if ($runInfo -and $runInfo.Output) { [IO.File]::WriteAllLines($runTxt, [string[]]$runInfo.Output) }
    if (Test-Path $runTxt) {
        $md.Add(""); $md.Add("## Run (tools\long-run.ps1 output, run.txt)"); $md.Add("")
        $md.Add('```'); Get-Content $runTxt | ForEach-Object { $md.Add($_) }; $md.Add('```')
    }
    [IO.File]::WriteAllText((Join-Path $reportDir "report.md"), ($md -join "`n") + "`n", (New-Object Text.UTF8Encoding $false))
    $sum
}

# ---------------------------------------------------------------- self-test

if ($SelfTest) {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $cfg = Get-GameConfig $Config
    Check ($cfg._root -and (Test-Path (Join-Path $cfg._root '.git'))) "main root resolved: $($cfg._root)"
    Check ((Resolve-GamePath $cfg 'runs') -like "$($cfg._root)\*") "paths resolve under the main root"
    $names = @($cfg.checkpoints.PSObject.Properties.Name)
    Check ($names.Count -ge 1) "checkpoints: $($names -join ', ')"
    foreach ($n in $names) {
        try {
            $cp = Get-Checkpoint $cfg $n; $t = Get-DumpTicks $cp
            $refOk = (-not $cp.reference) -or (Test-Path (Join-Path (Resolve-GamePath $cfg 'reference') $cp.reference))
            Check ($t.Count -gt 0 -and $refOk) "checkpoint $n tick $($cp.tick): $($t.Count) dumps $($t[0])..$($t[-1]), reference $(if ($cp.reference) { if ($refOk) { 'present' } else { 'MISSING' } } else { 'none' })"
            $rt = Get-Setting $cp 'route' $null
            if ($rt) { $ps = Get-RoutePadScript $rt $cfg._path; Check ($ps -match '^\d+:') "checkpoint $n route ${rt}: $ps" }
        } catch { Check $false "checkpoint ${n}: $_" }
    }
    # MISSING FUNCTION lines from a sample stderr.
    $mt = Join-Path ([IO.Path]::GetTempPath()) ("wall-missing-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    [IO.File]::WriteAllLines("$mt.err.txt", [string[]]@("[run:tick] x", "[guest-branch:missing-target] kind=IndirectCall op=jalr source=0x1bdb24 target=0x1bdb20 pc=0x1bdb20 ra=0x1bd9f8 sp=0x1ffe0 policy=1 thread=3 sourceName=FUN_001bdb00+0x24 raName=FUN_001bd900+0xf8 trace=0x1bd900 (FUN_001bd900)"))
    $ms = Get-LogStats $mt
    Remove-Item -Force "$mt.err.txt" -ErrorAction SilentlyContinue
    $ml = if ($ms.Missing.Count) { Format-MissingTarget $ms.Missing[0] } else { "" }
    Check ($ms.Missing.Count -eq 1 -and $ml -match '^MISSING FUNCTION target=0x1bdb20 from source/pc 0x1bdb24 \(FUN_001bdb00\+0x24\) ra 0x1bd9f8 \(FUN_001bd900\+0xf8\) thread 3 .*codeptrs\.py') "missing-target parsed from a sample stderr: $ml"
    $e = ConvertFrom-EnvList "A=1,PS2X_TRACE_STUBS=Sif,Cd,B=x=y"
    Check ($e['A'] -eq '1' -and $e['PS2X_TRACE_STUBS'] -eq 'Sif,Cd' -and $e['B'] -eq 'x=y') "-Env parsing (commas inside a value)"
    $steps = @(Get-BuildSteps $cfg $true $true)
    $dry = @(Invoke-BuildSteps $steps $true)
    Check ($steps.Count -ge 1 -and ($dry -join "`n") -notmatch '\{') "build steps expand ($($steps.Count)): no placeholders left"
    $dry | ForEach-Object { "      $_" }
    # Feature detection on a fake runner.
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("wall-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $fake = Join-Path $tmp "runner.exe"
    [IO.File]::WriteAllBytes($fake, [Text.Encoding]::ASCII.GetBytes("MZ junk PS2X_FASTFORWARD junk"))
    $fx = Get-RunnerFeatures $cfg $fake (Join-Path $tmp "features.json")
    Check ($fx['fastforward'].Supported -and -not $fx['snapshot'].Supported) "feature detection: fastforward yes, snapshot no on a fake runner"
    $fx2 = Get-RunnerFeatures $cfg $fake (Join-Path $tmp "features.json")
    Check ($fx2['fastforward'].Supported -and (Test-Path (Join-Path $tmp "features.json"))) "feature cache"
    $none = @{ fastforward = [pscustomobject]@{ Env = 'PS2X_FASTFORWARD'; Supported = $false; How = 'test'; Cfg = $null } }
    $pl = Get-RunPlan $cfg (Get-Checkpoint $cfg 'logo') $none 0 '' $false
    Check (-not $pl.Refused -and $pl.Need -le 90 -and $pl.Seconds -ge $pl.Need -and $pl.Env.Count -eq 0) "logo plan: ~$($pl.Need) s real time, limit $($pl.Seconds) s"
    if ($cfg.checkpoints.PSObject.Properties['title']) {
        $pt = Get-RunPlan $cfg (Get-Checkpoint $cfg 'title') $none 0 '' $false
        Check ([bool]$pt.Refused) "title at real time is refused (~$($pt.Need) s)"
        $pf = Get-RunPlan $cfg (Get-Checkpoint $cfg 'title') $fx 0 '' $false
        Check (-not $pf.Refused -and $pf.Env['PS2X_FASTFORWARD'] -eq "$($fx['fastforward'].Cfg.value)") "title with fast-forward: ~$($pf.Need) s, PS2X_FASTFORWARD=$($pf.Env['PS2X_FASTFORWARD'])"
    }
    # Report generation on an existing run's dumps.
    $runs = Resolve-GamePath $cfg 'runs'
    $src = if ($From) { (Resolve-Path $From).Path } else { Join-Path $runs "run43dump" }
    if (Test-Path (Join-Path $src 'gsdump_*.rgba')) {
        $nm = (Split-Path -Leaf $src) -replace 'dump$', ''
        $lb = Join-Path (Split-Path -Parent $src) $nm
        $rd = Join-Path $tmp "report"
        $sum = @(New-WallReport -cfg $cfg -cpName 'logo' -cp (Get-Checkpoint $cfg 'logo') -name $nm -dumpDir $src -logBase $lb -reportDir $rd -doCompare $true -runInfo $null)
        "      --- summary ---"
        $sum | ForEach-Object { "      $_" }
        $md = if (Test-Path (Join-Path $rd 'report.md')) { Get-Content -Raw (Join-Path $rd 'report.md') } else { "" }
        Check ($sum.Count -eq 10) "summary has 10 lines"
        Check ($md -match '## Contact sheet' -and $md -match '## Log summary' -and $md -match '## Regression') "report.md written with its sections"
        Check ((Test-Path (Join-Path $rd 'sheet.png')) -and (Test-Path (Join-Path $rd 'side.png'))) "sheet.png and side.png written"
        Check ($sum[4] -match 'best tick \d+ mad') "reference compared: $($sum[4])"
        Check ($sum[5] -match '^regression: (PASS|FAIL) \(') "regression scored: $($sum[5])"
        Check ($sum[1] -match 'vsync \d+') "log summary parsed: $($sum[1])"
    } else { Check $false "no dumps at $src for the report test (pass -From <dump dir>)" }
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}

# ---------------------------------------------------------------- main

$cfg = Get-GameConfig $Config
if ($List) {
    foreach ($p in $cfg.checkpoints.PSObject.Properties) {
        $t = Get-DumpTicks $p.Value
        $rt = Get-Setting $p.Value 'route' $null
        "{0,-15} tick {1,6}  dumps {2}..{3} ({4})  ref {5}{6}" -f $p.Name, $p.Value.tick, $t[0], $t[-1], $t.Count, $(if ($p.Value.reference) { $p.Value.reference } else { '-' }), $(if ($rt) { "  route $rt" } else { '' })
    }
    exit 0
}
$runs = Resolve-GamePath $cfg 'runs'
$cp = if ($Checkpoint) { Get-Checkpoint $cfg $Checkpoint } else { $null }

if ($NoRun) {
    if (-not $From) {
        if (-not $Name) { throw "-NoRun needs -From <dump dir> or -Name <run>" }
        $From = Join-Path $runs "${Name}dump"
    }
    $dumpDir = (Resolve-Path $From).Path.TrimEnd('\')
    $base = (Split-Path -Leaf $dumpDir) -replace 'dump$', ''
    if (-not $Name) { $Name = $base }
    $logBase = if ($Logs) { $Logs -replace '\.(out|err)\.txt$', '' } else { Join-Path (Split-Path -Parent $dumpDir) $base }
    $reportDir = Join-Path $runs $Name
    $sum = New-WallReport -cfg $cfg -cpName $Checkpoint -cp $cp -name $Name -dumpDir $dumpDir -logBase $logBase -reportDir $reportDir -doCompare ([bool]$Compare) -runInfo $null
    ""; "== wall summary =="; $sum
    Write-MissingTargets $script:WallMissing
    exit 0
}

if (-not $Name) { throw "-Name runN is required" }
if (-not $cp) { throw "-Checkpoint is required (see -List)" }
$reportDir = Join-Path $runs $Name

# 1. build
$steps = @(Get-BuildSteps $cfg ([bool]$Recomp) ([bool]$Build))
if ($steps.Count) { Invoke-BuildSteps $steps ([bool]$DryRun) }

# 2. run
$exe = Resolve-GamePath $cfg 'runner'
if (-not (Test-Path $exe) -and -not $DryRun) { throw "runner not found: $exe" }
$feats = Get-RunnerFeatures $cfg $exe (Join-Path $runs "wall-features.cache.json")
$plan = Get-RunPlan $cfg $cp $feats $Seconds $FastForward ([bool]$Force)
$envAll = [ordered]@{}
$re = Get-Setting $cfg.runner 'env' $null
if ($re) { foreach ($p in $re.PSObject.Properties) { $envAll[$p.Name] = "$($p.Value)" } }
foreach ($k in $plan.Env.Keys) { $envAll[$k] = $plan.Env[$k] }
$cpRoute = Get-Setting $cp 'route' $null
if ($cpRoute) { $envAll['PS2X_PAD_SCRIPT'] = "@route:$cpRoute" }
if ($Env) { $x = ConvertFrom-EnvList $Env; foreach ($k in $x.Keys) { $envAll[$k] = $x[$k] } }
if ($Route) { $envAll['PS2X_PAD_SCRIPT'] = "@route:$Route" }
if ($envAll.Contains('PS2X_PAD_SCRIPT') -and "$($envAll['PS2X_PAD_SCRIPT'])".StartsWith('@')) {
    $pv = "$($envAll['PS2X_PAD_SCRIPT'])"
    $rn = if ($pv.StartsWith('@route:')) { $pv.Substring(7) } else { $pv }
    $envAll['PS2X_PAD_SCRIPT'] = Get-RoutePadScript $rn $cfg._path
    $plan.Notes += "pad: route $rn -> $($envAll['PS2X_PAD_SCRIPT'])"
}
$trace = ""
if ($envAll.Contains('PS2X_TRACE_STUBS')) { $trace = $envAll['PS2X_TRACE_STUBS']; $envAll.Remove('PS2X_TRACE_STUBS') }

$longRun = Join-Path $PSScriptRoot "long-run.ps1"
$lrParams = (Get-Command $longRun).Parameters
$lrArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $longRun, "-Name", $Name, "-Seconds", $plan.Seconds,
            "-DumpList", ($plan.Ticks -join ','), "-StallSeconds", $StallSeconds)
if ($trace) { $lrArgs += @("-Trace", $trace) }
# Newer long-run.ps1 versions take these; pass them so their defaults don't override ours.
if ($lrParams.ContainsKey('FastForward')) {
    $ffv = if ($feats['fastforward'] -and $envAll.Contains($feats['fastforward'].Env)) { $envAll[$feats['fastforward'].Env] } else { '1' }
    $lrArgs += @("-FastForward", $ffv)
}
if ($lrParams.ContainsKey('Exe')) { $lrArgs += @("-Exe", $exe) }
if ($Headless) {
    if ($lrParams.ContainsKey('Headless')) { $lrArgs += "-Headless" } else { $envAll['PS2X_HEADLESS'] = '1' }
}
# No audio device unless -Sound (long-run.ps1 does the same; set here too so the plan shows it and
# an -Env/config PS2X_MUTE=1 cannot turn sound on).
if ($Sound) {
    $envAll['PS2X_NO_AUDIO'] = '0'; $envAll['PS2X_MUTE'] = '0'
    if ($lrParams.ContainsKey('Sound')) { $lrArgs += "-Sound" }
} elseif (-not $envAll.Contains('PS2X_NO_AUDIO')) {
    $envAll['PS2X_NO_AUDIO'] = '1'
}

"== plan: checkpoint $Checkpoint (tick $($cp.tick)), dumps $($plan.Ticks[0])..$($plan.Ticks[-1]) ($($plan.Ticks.Count))"
$plan.Notes | ForEach-Object { "   $_" }
"   env: " + (($envAll.Keys | ForEach-Object { "$_=$($envAll[$_])" }) -join ' ') + $(if ($trace) { " (trace: $trace)" })
if ($plan.Refused) { "REFUSED: $($plan.Refused)"; exit 2 }
if ($DryRun) { "[dry-run] powershell " + (($lrArgs | ForEach-Object { if ("$_" -match '\s') { "`"$_`"" } else { "$_" } }) -join ' '); exit 0 }

foreach ($k in $envAll.Keys) { Set-Item -Path "Env:$k" -Value $envAll[$k] }
$dumpDir = Join-Path $runs "${Name}dump"
if (Test-Path (Join-Path $dumpDir 'gsdump_*')) { "note: $dumpDir already has dumps; new ones are added beside them" }
$runOut = @(Invoke-Quiet powershell $lrArgs -Echo)
$how = ($runOut | Where-Object { $_ -match '^run:\s+' } | Select-Object -Last 1) -replace '^run:\s+', ''
$runInfo = [pscustomobject]@{ How = "run $how"; Notes = $plan.Notes; Output = $runOut }
$sum = New-WallReport -cfg $cfg -cpName $Checkpoint -cp $cp -name $Name -dumpDir $dumpDir -logBase (Join-Path $runs $Name) -reportDir $reportDir -doCompare ([bool]$Compare) -runInfo $runInfo
""; "== wall summary =="; $sum
Write-MissingTargets $script:WallMissing
