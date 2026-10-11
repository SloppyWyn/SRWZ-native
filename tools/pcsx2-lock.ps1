# One PCSX2 at a time for our automation (two instances fight over memory cards and settings).
# Lock file: local\pcsx2.lock, one line "<pid> <ISO time> <owner>", created atomically.
# The lock is held while that pid is alive; a lock whose pid is gone is stale and taken over.
# PowerShell: dot-source it, then
#     . "$PSScriptRoot\pcsx2-lock.ps1"
#     if (-not (Enter-Pcsx2Lock -Owner "my-script" -WaitSeconds 900)) { exit 1 }
#     try { ... run PCSX2 ... } finally { Exit-Pcsx2Lock }
# Other languages / a shell: the same file protocol (create with O_EXCL / CreateNew, write
# "<pid> <time> <owner>", delete when done), or this script as a command, holding the lock
# for another process id:
#     powershell -File tools\pcsx2-lock.ps1 -Acquire -Owner drive.py -HolderPid 1234 [-WaitSeconds 900]
#     powershell -File tools\pcsx2-lock.ps1 -Release -HolderPid 1234
#     powershell -File tools\pcsx2-lock.ps1 -Status
#     powershell -File tools\pcsx2-lock.ps1 -SelfTest
# Exit code 0 = done, 1 = timed out / not the holder. Before launching, a holder must also
# refuse to start while any pcsx2-qt.exe is running (it isn't one of ours: we hold the lock).
param([switch]$Acquire, [switch]$Release, [switch]$Status, [switch]$SelfTest,
      [string]$Owner = "", [int]$HolderPid = 0, [int]$WaitSeconds = 900, [string]$LockPath = "")

if (-not $LockPath) { $LockPath = Join-Path (Split-Path -Parent $PSScriptRoot) "local\pcsx2.lock" }
$script:Pcsx2LockPath = $LockPath

function Get-Pcsx2Lock {
    # Returns $null when free, else @{ Pid; Time; Owner; Alive }.
    try { $line = [IO.File]::ReadAllText($script:Pcsx2LockPath).Trim() } catch { return $null }
    $p = $line -split " ", 3
    $id = 0; [int]::TryParse($p[0], [ref]$id) | Out-Null
    $alive = $id -gt 0 -and [bool](Get-Process -Id $id -ErrorAction SilentlyContinue)
    [pscustomobject]@{ Pid = $id; Time = $p[1]; Owner = $(if ($p.Count -gt 2) { $p[2] } else { "" }); Alive = $alive }
}

function Enter-Pcsx2Lock([string]$Owner = "", [int]$WaitSeconds = 900, [int]$HolderPid = $PID) {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    $said = $false
    while ($true) {
        try {
            $fs = [IO.File]::Open($script:Pcsx2LockPath, [IO.FileMode]::CreateNew, [IO.FileAccess]::Write, [IO.FileShare]::None)
            $b = [Text.Encoding]::ASCII.GetBytes(("{0} {1:yyyy-MM-ddTHH:mm:ss} {2}" -f $HolderPid, (Get-Date), $Owner))
            $fs.Write($b, 0, $b.Length); $fs.Close()
            return $true
        } catch [IO.IOException] {
            $l = Get-Pcsx2Lock
            if ($l -and -not $l.Alive) {
                Write-Host ("pcsx2-lock: taking over a stale lock (pid {0}, {1}, {2})" -f $l.Pid, $l.Time, $l.Owner)
                Remove-Item $script:Pcsx2LockPath -Force -ErrorAction SilentlyContinue
                continue
            }
            if ($l -and $l.Pid -eq $HolderPid) { return $true }
            if (-not $said -and $l) { Write-Host ("pcsx2-lock: held by pid {0} ({1}, since {2}); waiting" -f $l.Pid, $l.Owner, $l.Time); $said = $true }
            if ((Get-Date) -gt $deadline) { Write-Host "pcsx2-lock: timed out"; return $false }
            Start-Sleep -Milliseconds 500
        }
    }
}

function Exit-Pcsx2Lock([int]$HolderPid = $PID) {
    $l = Get-Pcsx2Lock
    if ($l -and $l.Pid -eq $HolderPid) { Remove-Item $script:Pcsx2LockPath -Force -ErrorAction SilentlyContinue; return $true }
    return $false
}

if ($MyInvocation.InvocationName -ne ".") {
    if ($HolderPid -eq 0) { $HolderPid = $PID }
    if ($Acquire) { if (Enter-Pcsx2Lock -Owner $Owner -WaitSeconds $WaitSeconds -HolderPid $HolderPid) { exit 0 } else { exit 1 } }
    if ($Release) { if (Exit-Pcsx2Lock -HolderPid $HolderPid) { exit 0 } else { "not the holder"; exit 1 } }
    if ($Status) { $l = Get-Pcsx2Lock; if ($l) { $l } else { "free" }; exit 0 }
    if ($SelfTest) {
        $script:Pcsx2LockPath = Join-Path ([IO.Path]::GetTempPath()) ("pcsx2-lock-test-{0}.lock" -f $PID)
        $ok = $true
        function Check($c, $m) { if ($c) { "ok   $m" } else { "FAIL $m"; $script:ok = $false } }
        Check (Enter-Pcsx2Lock -Owner t1 -WaitSeconds 1) "acquire free lock"
        Check ((Get-Pcsx2Lock).Pid -eq $PID) "lock names our pid"
        # Held by a live other process (pid 4 = System is always alive): times out.
        Set-Content $script:Pcsx2LockPath "4 2026-01-01T00:00:00 other" -NoNewline
        Check (-not (Enter-Pcsx2Lock -Owner t2 -WaitSeconds 1)) "live holder blocks"
        Check (-not (Exit-Pcsx2Lock)) "cannot release someone else's lock"
        # Stale (pid that does not exist): taken over.
        Set-Content $script:Pcsx2LockPath "999999 2026-01-01T00:00:00 dead" -NoNewline
        Check (Enter-Pcsx2Lock -Owner t3 -WaitSeconds 1) "stale lock taken over"
        Check (Exit-Pcsx2Lock) "release"
        Check (-not (Test-Path $script:Pcsx2LockPath)) "file gone"
        if ($ok) { "selftest passed"; exit 0 } else { "selftest FAILED"; exit 1 }
    }
    "usage: see the header"; exit 1
}
