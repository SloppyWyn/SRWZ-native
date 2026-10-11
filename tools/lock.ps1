# Named cross-process locks for our automation, shared by every worktree and session.
# A lock is local\<Name>.lock in the MAIN checkout (found through git's common dir, so a
# worktree under local\wt-* uses the same file), one line "<pid> <ISO time> <owner>",
# created atomically. It is held while that pid is alive; a lock whose pid is gone is stale
# and taken over. Waiters queue first-come first-served: each drops a ticket in
# local\<Name>.lock.queue\ and tries the lock only when its ticket is the oldest live one.
# Names in use:
#     build   any build of ps2EntryRunner (the full game build needs most of the RAM:
#             one at a time across all sessions and worktrees)
# PowerShell: dot-source it, then
#     . "$PSScriptRoot\lock.ps1"
#     if (-not (Enter-NamedLock build -Owner "my-script" -WaitSeconds 3600)) { exit 1 }
#     try { ... } finally { Exit-NamedLock build }
# From a .bat (the lock is held for the batch file's own cmd.exe, so it is released even if
# the build crashes: a dead holder is stale):
#     powershell -NoProfile -File "%~dp0lock.ps1" -Acquire -Name build -Owner build-game -HolderParent
#     ... & powershell -NoProfile -File "%~dp0lock.ps1" -Release -Name build -HolderParent
# A holder's children pass through when it sets SRWZ_LOCK_HELD_<NAME>=<holder pid> for them.
# Command line:
#     powershell -File tools\lock.ps1 -Acquire -Name build -Owner x [-HolderPid N | -HolderParent] [-WaitSeconds 3600]
#     powershell -File tools\lock.ps1 -Release -Name build [-HolderPid N | -HolderParent]
#     powershell -File tools\lock.ps1 -Status [-Name build]
#     powershell -File tools\lock.ps1 -SelfTest
# Exit code 0 = done, 1 = timed out / not the holder.
param([switch]$Acquire, [switch]$Release, [switch]$Status, [switch]$SelfTest,
      [string]$Name = "build", [string]$Owner = "", [int]$HolderPid = 0, [switch]$HolderParent,
      [int]$WaitSeconds = 3600, [string]$LockDir = "")

function Get-LockDir {
    if ($script:LockDirOverride) { return $script:LockDirOverride }
    $common = & git -C $PSScriptRoot rev-parse --path-format=absolute --git-common-dir 2>$null
    # git prints C:/x/.git: backslashes, so ticket paths compare equal to Get-ChildItem's FullName
    # (with forward slashes every waiter saw itself queued behind its own ticket, forever).
    if ($LASTEXITCODE -eq 0 -and $common) { $root = Split-Path -Parent ($common.Trim() -replace '/', '\') }
    else { $root = Split-Path -Parent $PSScriptRoot }
    $d = Join-Path $root "local"
    if (-not (Test-Path $d)) { New-Item -ItemType Directory -Path $d | Out-Null }
    $d
}
$script:LockDirOverride = $LockDir

function Get-LockPath([string]$Name) { Join-Path (Get-LockDir) "$Name.lock" }

function Get-NamedLock([string]$Name) {
    # Returns $null when free, else @{ Pid; Time; Owner; Alive }.
    try { $line = [IO.File]::ReadAllText((Get-LockPath $Name)).Trim() } catch { return $null }
    $p = $line -split " ", 3
    $id = 0; [int]::TryParse($p[0], [ref]$id) | Out-Null
    $alive = $id -gt 0 -and [bool](Get-Process -Id $id -ErrorAction SilentlyContinue)
    [pscustomobject]@{ Name = $Name; Pid = $id; Time = $p[1]; Owner = $(if ($p.Count -gt 2) { $p[2] } else { "" }); Alive = $alive }
}

function Enter-NamedLock([string]$Name, [string]$Owner = "", [int]$WaitSeconds = 3600, [int]$HolderPid = $PID) {
    $path = Get-LockPath $Name
    # Nested use: a script holding the lock sets SRWZ_LOCK_HELD_<NAME>=<its pid> for its children
    # (tools\verify-merge.ps1 calling build-game.bat); while that pid holds the lock they pass
    # straight through (before the queue, or they would wait on their own parent), and their
    # -Release leaves the holder's lock alone (not the holder).
    $held = [Environment]::GetEnvironmentVariable("SRWZ_LOCK_HELD_" + $Name.ToUpper())
    if ($held) { $l = Get-NamedLock $Name; if ($l -and $l.Alive -and "$($l.Pid)" -eq $held) { return $true } }
    # Before a build queues: end leftover processes of our earlier builds and runs (orphaned
    # compilers, stuck ones), never the lock holder's tree (tools\reap.ps1).
    if ($Name -eq 'build') {
        $reap = Join-Path $PSScriptRoot 'reap.ps1'
        if (Test-Path $reap) { try { & $reap -Quiet | Out-Null } catch { } }
    }
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $said = $false
    # FIFO queue: each waiter drops a ticket "<utc ticks>-<pid>" in <Name>.queue\ and may only
    # try the lock while its ticket is the oldest one whose pid is alive (dead waiters' tickets
    # are removed), so a long wait is not starved by later arrivals.
    $qdir = "$path.queue"
    if (-not (Test-Path $qdir)) { New-Item -ItemType Directory -Path $qdir -Force | Out-Null }
    $ticket = Join-Path $qdir ("{0:D20}-{1}" -f [DateTime]::UtcNow.Ticks, $HolderPid)
    Set-Content -Path $ticket -Value $Owner -NoNewline
    try {
    while ($true) {
        $first = $null
        foreach ($t in (Get-ChildItem $qdir -File | Sort-Object Name)) {
            $tp = 0; [int]::TryParse(($t.Name -split "-")[1], [ref]$tp) | Out-Null
            if ($tp -gt 0 -and (Get-Process -Id $tp -ErrorAction SilentlyContinue)) { $first = $t.FullName; break }
            Remove-Item $t.FullName -Force -ErrorAction SilentlyContinue
        }
        if (-not (Test-Path $ticket)) { Set-Content -Path $ticket -Value $Owner -NoNewline; continue }
        if ((Split-Path -Leaf "$first") -ne (Split-Path -Leaf $ticket)) {
            if (-not $said) { Write-Host ("lock {0}: queued behind {1}; waiting" -f $Name, (Split-Path -Leaf $first)); $said = $true }
            if ((Get-Date) -gt $deadline) { Write-Host "lock ${Name}: timed out"; return $false }
            Start-Sleep -Milliseconds 500
            continue
        }
        try {
            $fs = [IO.File]::Open($path, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $b = [Text.Encoding]::ASCII.GetBytes(("{0} {1:yyyy-MM-ddTHH:mm:ss} {2}" -f $HolderPid, (Get-Date), $Owner))
            $fs.Write($b, 0, $b.Length); $fs.Close()
            return $true
        } catch [IO.IOException] {
            $l = Get-NamedLock $Name
            if ($l -and -not $l.Alive) {
                Write-Host ("lock {0}: taking over a stale lock (pid {1}, {2}, {3})" -f $Name, $l.Pid, $l.Time, $l.Owner)
                Remove-Item $path -Force -ErrorAction SilentlyContinue
                continue
            }
            if ($l -and $l.Pid -eq $HolderPid) { return $true }
            if (-not $said -and $l) { Write-Host ("lock {0}: held by pid {1} ({2}, since {3}); waiting" -f $Name, $l.Pid, $l.Owner, $l.Time); $said = $true }
            if ((Get-Date) -gt $deadline) { Write-Host "lock ${Name}: timed out"; return $false }
            Start-Sleep -Milliseconds 500
        }
    }
    } finally { Remove-Item $ticket -Force -ErrorAction SilentlyContinue }
}

function Exit-NamedLock([string]$Name, [int]$HolderPid = $PID) {
    $l = Get-NamedLock $Name
    if ($l -and $l.Pid -eq $HolderPid) { Remove-Item (Get-LockPath $Name) -Force -ErrorAction SilentlyContinue; return $true }
    return $false
}

if ($MyInvocation.InvocationName -ne ".") {
    if ($HolderParent) {
        $HolderPid = [int](Get-CimInstance Win32_Process -Filter "ProcessId=$PID").ParentProcessId
    }
    if ($HolderPid -eq 0) { $HolderPid = $PID }
    if ($Acquire) { if (Enter-NamedLock $Name -Owner $Owner -WaitSeconds $WaitSeconds -HolderPid $HolderPid) { exit 0 } else { exit 1 } }
    if ($Release) { if (Exit-NamedLock $Name -HolderPid $HolderPid) { exit 0 } else { "not the holder"; exit 1 } }
    if ($Status) { $l = Get-NamedLock $Name; if ($l) { $l } else { "$Name free" }; exit 0 }
    if ($SelfTest) {
        $script:LockDirOverride = [IO.Path]::GetTempPath()
        $n = "selftest-$PID"
        $ok = $true
        function Check($c, $m) { if ($c) { "ok   $m" } else { "FAIL $m"; $script:ok = $false } }
        Check (Enter-NamedLock $n -Owner t1 -WaitSeconds 1) "acquire free lock"
        Check ((Get-NamedLock $n).Pid -eq $PID) "lock names our pid"
        Set-Content (Get-LockPath $n) "4 2026-01-01T00:00:00 other" -NoNewline
        Check (-not (Enter-NamedLock $n -Owner t2 -WaitSeconds 1)) "live holder blocks"
        Check (-not (Exit-NamedLock $n)) "cannot release someone else's lock"
        Set-Content (Get-LockPath $n) "999999 2026-01-01T00:00:00 dead" -NoNewline
        Check (Enter-NamedLock $n -Owner t3 -WaitSeconds 1) "stale lock taken over"
        Check (Exit-NamedLock $n) "release"
        Check (-not (Test-Path (Get-LockPath $n))) "file gone"
        # FIFO: an older ticket of a live waiter (pid 4) keeps us out of a free lock...
        $q = "$(Get-LockPath $n).queue"
        Set-Content (Join-Path $q ("{0:D20}-4" -f 1)) "older" -NoNewline
        Check (-not (Enter-NamedLock $n -Owner t4 -WaitSeconds 1)) "older live ticket goes first"
        Check (-not (Test-Path (Get-LockPath $n))) "lock left free for it"
        # ...and a dead waiter's ticket is dropped.
        Remove-Item (Join-Path $q ("{0:D20}-4" -f 1))
        Set-Content (Join-Path $q ("{0:D20}-999999" -f 1)) "dead" -NoNewline
        Check (Enter-NamedLock $n -Owner t5 -WaitSeconds 2) "dead ticket skipped"
        Check ((Get-ChildItem $q -File).Count -eq 0) "queue empty after acquire"
        # Nested: a child of the holder (SRWZ_LOCK_HELD_<NAME> = holder pid) passes while it holds.
        $var = "SRWZ_LOCK_HELD_" + $n.ToUpper()
        [Environment]::SetEnvironmentVariable($var, "$PID")
        Check (Enter-NamedLock $n -Owner child -WaitSeconds 1 -HolderPid 4) "nested child passes while the holder holds"
        Check (-not (Exit-NamedLock $n -HolderPid 4)) "nested child's release leaves the lock"
        [Environment]::SetEnvironmentVariable($var, "4")
        Check (-not (Enter-NamedLock $n -Owner child -WaitSeconds 1 -HolderPid 4)) "wrong holder pid in the variable: waits"
        [Environment]::SetEnvironmentVariable($var, $null)
        Check (Exit-NamedLock $n) "release 2"
        Remove-Item $q -Recurse -Force
        $script:LockDirOverride = ""
        Check ((Get-LockDir) -like "*SRWZ-native\local") "lock dir is the main checkout's local (got $(Get-LockDir))"
        if ($ok) { "selftest passed"; exit 0 } else { "selftest FAILED"; exit 1 }
    }
    "usage: see the header"; exit 1
}
