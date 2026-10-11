# Ends leftover processes of OUR builds and runs that are stuck or orphaned, with their whole
# tree. Never matches by image name alone: a process is ours only when its command line or
# executable is under the project folder (C:\dev\SRWZ-native or D:\dev\SRWZ-native), or for a
# compiler (cl/link, whose command line is a response file) when that response file names the
# project folder, or when an ancestor of it is ours. Never touches anything else (other
# apps and projects on the PC).
#
# What is ended (each with its descendants), unless it is the build-lock holder or under it:
#   - orphans: a build tool (cl, link, MSBuild, Tracker, VBCSCompiler, cmake, ps2_recomp,
#     python running our tools) whose parent process is gone;
#   - stuck compilers: cl.exe past -CompilerCpuCap CPU-seconds (default 3600);
#   - runners and PCSX2 capture runs (ps2EntryRunner.exe, pcsx2-qt.exe with our local\pcsx2-auto
#     data folder) that are orphaned and older than -RunMaxMinutes (default 180).
#
#     powershell -File tools\reap.ps1 -List            what would be ended, and why (nothing is ended)
#     powershell -File tools\reap.ps1                  end them once, one log line per process
#     powershell -File tools\reap.ps1 -Watch           check every -IntervalSeconds (60) until stopped
#     tools\reap-watch.cmd                             (double-click) start -Watch hidden, below normal
#     powershell -File tools\reap.ps1 -SelfTest
# Log: local\reap.log (one line per process ended). tools\lock.ps1 runs it (quietly) before a
# build takes the build lock.
param(
    [switch]$List,
    [switch]$WhatIf,
    [switch]$Watch,
    [int]$IntervalSeconds = 60,
    [int]$CompilerCpuCap = 3600,
    [int]$RunMaxMinutes = 180,
    [switch]$Quiet,
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
$listOnly = [bool]($List -or $WhatIf)
. (Join-Path $PSScriptRoot "gameconfig.ps1")   # Get-MainRoot: the main checkout, also from a worktree
$mainRoot = Get-MainRoot
$roots = @('C:\dev\SRWZ-native', 'D:\dev\SRWZ-native')
$buildTools = @('cl.exe', 'link.exe', 'msbuild.exe', 'tracker.exe', 'vbcscompiler.exe', 'cmake.exe', 'ps2_recomp.exe',
                'ps2_vu1_aot.exe', 'python.exe', 'mspdbsrv.exe', 'conhost.exe', 'cmd.exe', 'powershell.exe')
$runTools = @('ps2entryrunner.exe', 'pcsx2-qt.exe')

function Test-UnderRoot([string]$text) {
    if (-not $text) { return $false }
    $t = $text.ToLowerInvariant().Replace('/', '\')
    foreach ($r in $roots) { if ($t.Contains($r.ToLowerInvariant())) { return $true } }
    return $false
}

# A compiler's command line is "@<response file>": ours when that file names the project folder.
function Test-ResponseFile([string]$commandLine) {
    if (-not $commandLine) { return $false }
    $m = [regex]::Match($commandLine, '@"?([^"\s]+\.(rsp|tmp|[0-9a-f]+))"?')
    if (-not $m.Success) { $m = [regex]::Match($commandLine, '@"?([A-Za-z]:\\[^"\s]+)"?') }
    if (-not $m.Success) { return $false }
    $path = $m.Groups[1].Value
    try {
        if (Test-Path -LiteralPath $path) {
            $raw = [IO.File]::ReadAllBytes($path)
            $text = if ($raw.Length -ge 2 -and $raw[0] -eq 0xFF -and $raw[1] -eq 0xFE) { [Text.Encoding]::Unicode.GetString($raw) } else { [Text.Encoding]::UTF8.GetString($raw) }
            return (Test-UnderRoot $text)
        }
    } catch { }
    return $false
}

# Pure classification over a process table (objects with Pid, Ppid, Name, CommandLine,
# ExecutablePath, CpuSeconds, StartTime): returns @{ Pid; Name; Reason } for each root to end.
function Get-ReapPlan($procs, [int]$lockPid, [datetime]$now, [scriptblock]$isOursByFile) {
    $byPid = @{}
    foreach ($p in $procs) { $byPid[[int]$p.Pid] = $p }
    $children = @{}
    foreach ($p in $procs) {
        $pp = [int]$p.Ppid
        if (-not $children.ContainsKey($pp)) { $children[$pp] = New-Object System.Collections.ArrayList }
        [void]$children[$pp].Add([int]$p.Pid)
    }
    # A parent counts only if it is alive and older than the child (pids are reused).
    $parentOf = {
        param($p)
        $par = $byPid[[int]$p.Ppid]
        if ($par -and $par.StartTime -le $p.StartTime) { return $par }
        return $null
    }
    $ours = @{}
    $direct = {
        param($p)
        (Test-UnderRoot $p.CommandLine) -or (Test-UnderRoot $p.ExecutablePath) -or
        ($p.Name.ToLowerInvariant() -in @('cl.exe', 'link.exe') -and (& $isOursByFile $p.CommandLine))
    }
    foreach ($p in $procs) {
        $q = $p; $hops = 0
        while ($q -and $hops -lt 32) {
            if (& $direct $q) { $ours[[int]$p.Pid] = $true; break }
            $q = & $parentOf $q; $hops++
        }
    }
    # The lock holder and everything under it are never ended.
    $protected = @{}
    if ($lockPid -gt 0 -and $byPid.ContainsKey($lockPid)) {
        $stack = New-Object System.Collections.Stack
        $stack.Push($lockPid)
        while ($stack.Count) {
            $id = $stack.Pop(); $protected[$id] = $true
            if ($children.ContainsKey($id)) { foreach ($c in $children[$id]) { $stack.Push($c) } }
        }
    }
    $plan = @()
    foreach ($p in $procs) {
        $id = [int]$p.Pid; $name = $p.Name.ToLowerInvariant()
        if (-not $ours[$id] -or $protected[$id]) { continue }
        $orphan = -not (& $parentOf $p)
        $reason = $null
        if ($name -in $buildTools -and $orphan -and $name -notin @('conhost.exe', 'cmd.exe', 'powershell.exe')) { $reason = "orphaned build tool (parent $($p.Ppid) gone)" }
        elseif ($name -eq 'cl.exe' -and $p.CpuSeconds -gt $CompilerCpuCap) { $reason = "compiler past $CompilerCpuCap CPU-s ($([int]$p.CpuSeconds) s)" }
        elseif ($name -in $runTools -and $orphan -and ($now - $p.StartTime).TotalMinutes -gt $RunMaxMinutes) { $reason = "orphaned run older than $RunMaxMinutes min" }
        if ($reason) {
            # Skip it when an ancestor is already in the plan (its tree covers it).
            $plan += [pscustomobject]@{ Pid = $id; Name = $p.Name; Reason = $reason }
        }
    }
    $planned = @{}; foreach ($x in $plan) { $planned[$x.Pid] = $true }
    $roots2 = @()
    foreach ($x in $plan) {
        $q = & $parentOf $byPid[$x.Pid]; $covered = $false
        while ($q) { if ($planned[[int]$q.Pid]) { $covered = $true; break }; $q = & $parentOf $q }
        if (-not $covered) { $roots2 += $x }
    }
    return , $roots2
}

function Get-Tree($procs, [int]$rootPid) {
    $kids = @{}
    foreach ($p in $procs) { $pp = [int]$p.Ppid; if (-not $kids.ContainsKey($pp)) { $kids[$pp] = @() }; $kids[$pp] += [int]$p.Pid }
    $out = New-Object System.Collections.ArrayList
    $walk = { param($id) foreach ($c in @($kids[$id])) { if ($c) { & $walk $c } }; [void]$out.Add($id) }
    & $walk $rootPid
    return $out   # leaves first, the root last
}

function Get-ProcessTable {
    $now = Get-Date
    Get-CimInstance Win32_Process | ForEach-Object {
        $cpu = 0.0
        try { $cpu = ([double]$_.KernelModeTime + [double]$_.UserModeTime) / 1e7 } catch { }
        $start = $now
        try { if ($_.CreationDate) { $start = [datetime]$_.CreationDate } } catch { }
        [pscustomobject]@{ Pid = [int]$_.ProcessId; Ppid = [int]$_.ParentProcessId; Name = "$($_.Name)"
                           CommandLine = "$($_.CommandLine)"; ExecutablePath = "$($_.ExecutablePath)"; CpuSeconds = $cpu; StartTime = $start }
    }
}

function Get-LockPid {
    $lock = Join-Path $mainRoot "local\build.lock"
    try { $line = [IO.File]::ReadAllText($lock).Trim(); $id = 0; [int]::TryParse(($line -split ' ')[0], [ref]$id) | Out-Null; return $id } catch { return 0 }
}

function Write-ReapLog([string]$text) {
    $log = Join-Path $mainRoot "local\reap.log"
    try { Add-Content -LiteralPath $log -Value ("{0:s} {1}" -f (Get-Date), $text) -Encoding utf8 } catch { }
}

function Invoke-Reap {
    $procs = @(Get-ProcessTable)
    $plan = Get-ReapPlan $procs (Get-LockPid) (Get-Date) { param($c) Test-ResponseFile $c }
    # Heartbeat: local\reap.status says when the last check ran (the watcher has no window).
    if (-not $listOnly) {
        $status = "{0:s} checked {1} processes: {2} (watcher pid {3})" -f (Get-Date), $procs.Count,
            $(if ($plan.Count) { "ending $($plan.Count)" } else { "nothing to end" }), $PID
        try { [IO.File]::WriteAllText((Join-Path $mainRoot "local\reap.status"), $status + "`r`n") } catch { }
    }
    if (-not $plan.Count) { if (-not $Quiet) { "reap: nothing to end" }; return }
    foreach ($x in $plan) {
        $tree = @(Get-Tree $procs $x.Pid)
        $line = "pid $($x.Pid) $($x.Name): $($x.Reason); tree $($tree.Count) process(es): $($tree -join ',')"
        if ($listOnly) { "would end $line"; continue }
        foreach ($id in $tree) { try { Stop-Process -Id $id -Force -ErrorAction Stop } catch { } }
        Write-ReapLog "ended $line"
        if (-not $Quiet) { "ended $line" }
    }
}

function Invoke-SelfTest {
    $script:ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $t0 = Get-Date '2026-10-08 20:00'
    function P($id, $ppid, $name, $cmd, $cpu = 1, $min = 10) {
        [pscustomobject]@{ Pid = $id; Ppid = $ppid; Name = $name; CommandLine = $cmd; ExecutablePath = "C:\Program Files\x\$name"; CpuSeconds = $cpu; StartTime = $t0.AddMinutes(-$min) }
    }
    $procs = @(
        (P 4 0 'System' '' 0 999),
        (P 100 4 'explorer.exe' 'explorer' 0 900),
        # an orphaned compiler tree of ours (parent 11360 gone), response file names the project
        (P 20412 11360 'cl.exe' '"CL.exe" @C:\Temp\MSBuildTemp\ours.rsp' 0 60),
        (P 20413 20412 'cl.exe' '"CL.exe" @C:\Temp\MSBuildTemp\ours.rsp' 7000 60),
        # someone else's orphaned compiler (response file not ours)
        (P 30000 9999 'cl.exe' '"CL.exe" @C:\Temp\MSBuildTemp\theirs.rsp' 9000 60),
        # OtherApp, orphaned, never ours
        (P 13520 9998 'OtherAppDevice.exe' '"C:\Program Files\OtherApp\OtherAppDevice.exe"' 99999 600),
        # the lock holder's live build (cl past the cap but protected)
        (P 21204 100 'powershell.exe' 'powershell -File tools/wt-build.ps1 -Worktree local/wt-x' 1 20),
        (P 21300 21204 'MSBuild.exe' 'MSBuild.exe C:\dev\SRWZ-native\local\wt-x\out\build\x.vcxproj' 5 19),
        (P 21400 21300 'cl.exe' '"CL.exe" @C:\Temp\MSBuildTemp\ours.rsp' 9000 18),
        # a live checkpoint run (parent alive) and an orphaned one from yesterday
        (P 22000 100 'ps2EntryRunner.exe' 'C:\dev\SRWZ-native\third_party\x\ps2EntryRunner.exe a b' 50 5),
        (P 22100 8888 'ps2EntryRunner.exe' 'C:\dev\SRWZ-native\third_party\x\ps2EntryRunner.exe a b' 50 600),
        # an orphaned runner of ours started 30 minutes ago (kept: may be a detached run)
        (P 22200 8887 'ps2EntryRunner.exe' 'C:\dev\SRWZ-native\third_party\x\ps2EntryRunner.exe a b' 50 30),
        # a stuck compiler of ours with a live non-lock parent (past the cap)
        (P 23000 100 'cl.exe' '"CL.exe" @C:\Temp\MSBuildTemp\ours.rsp' 4000 70)
    )
    $isOurs = { param($c) $c -like '*ours.rsp*' }
    $plan = Get-ReapPlan $procs 21204 $t0 $isOurs
    $ids = @($plan | ForEach-Object { $_.Pid })
    Check ($ids -contains 20412 -and $ids -notcontains 20413) "orphaned compiler tree ended from its root"
    Check ($ids -notcontains 30000) "someone else's compiler is left alone"
    Check ($ids -notcontains 13520) "OtherApp is never touched"
    Check ($ids -notcontains 21204 -and $ids -notcontains 21300 -and $ids -notcontains 21400) "the lock holder's build is protected"
    Check ($ids -notcontains 22000 -and $ids -contains 22100 -and $ids -notcontains 22200) "runners: live and recent orphan kept, old orphan ended"
    Check ($ids -contains 23000) "a compiler past the CPU cap is ended"
    Check ($ids -notcontains 100 -and $ids -notcontains 4) "system processes untouched"
    $tree = @(Get-Tree $procs 20412)
    Check ($tree.Count -eq 2 -and $tree[-1] -eq 20412) "tree: children first, root last"
    Check (-not (Test-UnderRoot 'C:\Program Files\OtherApp\x.exe') -and (Test-UnderRoot 'c:/DEV/srwz-native/local')) "root test is case and slash insensitive"
    if ($script:ok) { "reap selftest: PASS"; exit 0 } else { "reap selftest: FAIL"; exit 1 }
}

if ($SelfTest) { Invoke-SelfTest }
if ($Watch) {
    if (-not $Quiet) { "reap: watching every $IntervalSeconds s (log local\reap.log)" }
    Write-ReapLog "watch started (pid $PID, every $IntervalSeconds s)"
    while ($true) { Invoke-Reap; Start-Sleep -Seconds $IntervalSeconds }
}
Invoke-Reap
