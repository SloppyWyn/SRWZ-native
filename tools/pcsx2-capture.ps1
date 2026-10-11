# Boots the game in the reference emulator (PCSX2\pcsx2-qt.exe, disc\srwz.iso) and captures
# its window at given game seconds, for reference pictures without Wyn.
#     powershell -File tools\pcsx2-capture.ps1 -Name movie -Times 20,40,60 [-Keys "416:START,425:CROSS"]
#                [-Unlimited | -Turbo] [-Seconds N] [-Sound] [-ClockOffset s] [-State file.p2s [-StateTime s]]
# Output: local\reference\<Name>\pcsx2_<s>s.png, capture.log (actual times), emulog.txt.
#
# Game time. -Times, -Keys and -Seconds are game seconds from launch, as a real-time run
# sees them (intro timing in local\reference\NOTES.md). The clock is the game's own VBlank
# counter, read over PINE (tools\pine.py; the word at 0x59c1d8 counts 60 per second from
# early boot): game s = ClockOffset + count / 59.94, ClockOffset measured in a real-time
# run. So times hold at any emulation speed. Until the counter is readable (first ~5 s)
# and if PINE is unavailable, the clock is wall time x speed.
#   (default)   real time.
#   -Unlimited  PCSX2's -unlimited flag (no speed limit), until 3 game s before the first
#               event; then the ToggleFrameLimit hotkey ([Hotkeys], F4) drops it to 1x so
#               shots and key presses land on time. Measured 2026-10-06: the opening movie
#               (390 s) took 195 s in one run (2.0x) and 479 s in another (0.8x, machine
#               busy), so unlimited is no faster than -Turbo here and varies a lot; the
#               VBlank clock keeps the times right either way.
#   -Turbo      PCSX2's -turbo flag ([Framerate] TurboScalar, 2x): the whole run at 2x.
#   -State <file.p2s>  boot from a PCSX2 save state (flag -statefile) instead of a fresh
#               boot. The VBlank counter is part of the state, so -Times/-Keys stay absolute
#               game seconds. local\reference\states\pretitle_400s.p2s is game ~400 s (the end
#               of the opening movie; title ~412-424 s): a title capture takes ~25 s instead
#               of ~7 min. -StateTime (default 400) is the state's game time, used only for
#               the fallback wall clock. States come from the private config (docs\PCSX2-TOOLBOX.md).
#
# Settings. PCSX2 runs on a private data folder, local\pcsx2-auto (flag -datapath; made by
# tools\pcsx2-auto.ps1 from a copy of Wyn's settings), never on Wyn's own
# Documents\PCSX2: his settings, memory cards and window position are not touched.
# One PCSX2 at a time: the script takes local\pcsx2.lock (tools\pcsx2-lock.ps1), waiting
# while another of our scripts holds it, and refuses to start while any pcsx2-qt.exe runs.
#
# -Keys "<game s>:<key>,..." presses keys in the PCSX2 window. A key is a pad button name,
# sent as the keyboard key bound to it in the private config's [Pad1]:
#     START=Return CROSS=X CIRCLE=C TRIANGLE=V SQUARE=Z UP/DOWN/LEFT/RIGHT=arrow keys
# or any System.Windows.Forms.Keys name (e.g. F8), which reaches PCSX2's hotkeys.
# Wyn may take over at any time: while waiting and before each key press the script
# checks for input that isn't its own (GetLastInputInfo) and for another program's window
# coming to the front. Then it sends nothing until he has been idle for 10 s, and every key
# and shot from his first input until then is logged as SUSPECT in capture.log.
# Captures use PrintWindow, so the window may be covered (a minimised one is restored).
# PCSX2 runs with no audio output at all unless -Sound is given: the private config's
# [SPU2/Output] Backend = Null (tools\pcsx2-auto.ps1), so no audio device opens; tools\appaudio.ps1
# still mutes its session as a fallback, should a PCSX2 version ignore that key.
# Any dialog window PCSX2 opens (error boxes etc.) is logged with its class and title and
# closed, so an unattended run can't hang on it. The run ends by closing the main window.
param([Parameter(Mandatory)][string]$Name, [Parameter(Mandatory)][string[]]$Times,
      [int]$Seconds = 0, [string]$Keys = "", [switch]$Turbo, [switch]$Unlimited, [switch]$Sound,
      [double]$ClockOffset = 3.62, [string]$State = "", [double]$StateTime = 400, [switch]$PostKeys)
# -PostKeys: keys are posted as WM_KEYDOWN/WM_KEYUP messages to PCSX2's windows instead of
# typed with the window in front: no focus change, no waiting for Wyn to be idle.
# With -PostKeys a key may be a chord: "430:R1&START" (pad names L1 R1 L2 R2 SELECT too).
$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$exe = Join-Path $root "PCSX2\pcsx2-qt.exe"
$iso = Join-Path $root "disc\srwz.iso"
$out = Join-Path $root "local\reference\$Name"
New-Item -ItemType Directory -Force $out | Out-Null
$log = Join-Path $out "capture.log"
function Log($m) { $l = "{0:HH:mm:ss.fff} {1}" -f (Get-Date), $m; Write-Host $l; Add-Content -Path $log -Value $l }
. (Join-Path $PSScriptRoot "pcsx2-lock.ps1")
. (Join-Path $PSScriptRoot "pcsx2-auto.ps1")
. (Join-Path $PSScriptRoot "launch.ps1")  # Start-Quiet: PCSX2 shown without activation, no consoles
# "-File" passes "20,40" as one string: split it here.
$secs = [int[]]@($Times -join "," -split "," | Where-Object { $_ -ne "" } | ForEach-Object { [int]$_ })
$VsyncAddr = 0x59c1d8; $GameId = "SLPS-25887"
if ($State) {
    $State = (Resolve-Path $State).Path   # PCSX2 needs an absolute path
} else { $StateTime = 0 }
$dataPath = Initialize-Pcsx2Auto
Set-Pcsx2AutoAudio $dataPath -Sound:$Sound
$ini = Join-Path $dataPath "PCSX2\inis\PCSX2.ini"
function HotkeyName([string]$action, [string]$default) {
    $m = Select-String -Path $ini -Pattern "^\s*$action\s*=\s*Keyboard/(\w+)\s*$" | Select-Object -First 1
    if ($m) { $m.Matches[0].Groups[1].Value } else { $default }
}
# Wall-clock speed factor (fallback clock only).
$speed = 1.0
if ($Turbo -and -not $Unlimited) {
    $speed = 2.0
    $m = Select-String -Path $ini -Pattern '^\s*TurboScalar\s*=\s*([0-9.]+)' | Select-Object -First 1
    if ($m) { $speed = [double]$m.Matches[0].Groups[1].Value }
}

Add-Type -AssemblyName System.Drawing, System.Windows.Forms
$sig = @'
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")] public static extern bool PostMessage(IntPtr h, uint msg, IntPtr w, IntPtr l);
[DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
[DllImport("user32.dll")] public static extern bool ShowWindow(IntPtr h, int cmd);
[DllImport("user32.dll")] public static extern bool PrintWindow(IntPtr h, IntPtr hdc, uint flags);
[DllImport("user32.dll")] public static extern uint MapVirtualKey(uint code, uint type);
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
[DllImport("user32.dll")] public static extern bool GetLastInputInfo(ref LASTINPUTINFO lii);
[DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
[DllImport("user32.dll")] public static extern uint GetWindowThreadProcessId(IntPtr h, out uint pid);
[DllImport("kernel32.dll")] public static extern uint GetTickCount();
[DllImport("kernel32.dll")] public static extern uint GetCurrentThreadId();
[DllImport("user32.dll")] public static extern bool AttachThreadInput(uint a, uint b, bool attach);
[DllImport("user32.dll")] public static extern bool BringWindowToTop(IntPtr h);
[DllImport("user32.dll")] public static extern bool IsWindowVisible(IntPtr h);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetWindowText(IntPtr h, System.Text.StringBuilder s, int n);
[DllImport("user32.dll", CharSet=CharSet.Unicode)] public static extern int GetClassName(IntPtr h, System.Text.StringBuilder s, int n);
public delegate bool EnumProc(IntPtr h, IntPtr l);
[DllImport("user32.dll")] public static extern bool EnumWindows(EnumProc p, IntPtr l);
public static System.Collections.Generic.List<IntPtr> TopWindows(uint pid) {
    var r = new System.Collections.Generic.List<IntPtr>();
    EnumWindows((h, l) => { uint p; GetWindowThreadProcessId(h, out p); if (p == pid && IsWindowVisible(h)) r.Add(h); return true; }, IntPtr.Zero);
    return r;
}
[DllImport("user32.dll")] public static extern bool EnumChildWindows(IntPtr parent, EnumProc p, IntPtr l);
public static System.Collections.Generic.List<IntPtr> WindowTree(IntPtr top) {
    var r = new System.Collections.Generic.List<IntPtr>(); r.Add(top);
    EnumChildWindows(top, (h, l) => { r.Add(h); return true; }, IntPtr.Zero);
    return r;
}
public static string Describe(IntPtr h) {
    var t = new System.Text.StringBuilder(512); var c = new System.Text.StringBuilder(256);
    GetWindowText(h, t, 512); GetClassName(h, c, 256); return "[" + c + "] \"" + t + "\"";
}
public struct RECT { public int Left, Top, Right, Bottom; }
public struct LASTINPUTINFO { public uint cbSize; public uint dwTime; }
'@
if (-not ("Native.Win32Pcsx2c" -as [type])) { Add-Type -MemberDefinition $sig -Name Win32Pcsx2c -Namespace Native }
$W = [Native.Win32Pcsx2c]
$W::SetProcessDPIAware() | Out-Null

# Pad buttons -> the keyboard keys bound to them in the private config's [Pad1].
$PadKeys = @{}
foreach ($k in $script:Pcsx2AutoKeys.Keys) { $PadKeys[$k.ToUpper()] = $script:Pcsx2AutoKeys[$k] }

# ---- Wyn-activity watch. Input we inject ourselves is recognised by its time ($ownUntil).
$script:ownUntil = [int64]0
$script:lastUser = [int64]-1      # tick of his last input / window switch, -1 = none yet
$script:userSince = $false        # activity since the last logged event
$script:lastFg = $W::GetForegroundWindow()
$script:pcsx2Id = 0
$script:startTick = [int64]0
$script:h = [IntPtr]::Zero
$script:seenDialogs = @{}
function Tick { [int64]$W::GetTickCount() }
function Wall { ((Get-Date) - $script:launch).TotalSeconds }
function CheckUser {
    $now = Tick
    $lii = New-Object Native.Win32Pcsx2c+LASTINPUTINFO
    $lii.cbSize = 8
    $why = $null
    if ($W::GetLastInputInfo([ref]$lii)) {
        $t = [int64]$lii.dwTime
        if ($t -gt $script:ownUntil -and $t -gt $script:startTick -and $t -gt $script:lastUser) { $why = "input"; $script:lastUser = $t }
    }
    $fg = $W::GetForegroundWindow()
    if ($fg -ne $script:lastFg) {
        $fgPid = [uint32]0; $W::GetWindowThreadProcessId($fg, [ref]$fgPid) | Out-Null
        if ($fg -ne [IntPtr]::Zero -and $fgPid -ne $script:pcsx2Id -and $now -gt $script:ownUntil) { $why = "window switch to pid $fgPid"; $script:lastUser = $now }
        $script:lastFg = $fg
    }
    if ($why) {
        if (-not $script:userSince) { Log ("Wyn active ({0}) at wall {1:N2} s: no keys until he is idle 10 s" -f $why, (Wall)) }
        $script:userSince = $true
    }
}
function UserIdleMs { if ($script:lastUser -lt 0) { [int64]::MaxValue } else { (Tick) - $script:lastUser } }
# ---- Dialogs: any visible top-level PCSX2 window other than the main one is logged and closed.
function DialogText([IntPtr]$hwnd) {
    # Window title plus every text/button label inside the dialog (UI Automation), so the
    # message survives in capture.log after we close the dialog.
    try {
        Add-Type -AssemblyName UIAutomationClient, UIAutomationTypes -ErrorAction Stop
        $root = [Windows.Automation.AutomationElement]::FromHandle($hwnd)
        $parts = @("title='" + $root.Current.Name + "'")
        foreach ($e in $root.FindAll([Windows.Automation.TreeScope]::Descendants, [Windows.Automation.Condition]::TrueCondition)) {
            $n = $e.Current.Name
            if ($n) { $parts += $n.Replace("`r", " ").Replace("`n", " ") }
        }
        return ($parts -join " | ")
    } catch { return "(could not read: $($_.Exception.Message))" }
}
function CheckDialogs {
    if ($script:pcsx2Id -eq 0 -or $script:h -eq [IntPtr]::Zero) { return }
    foreach ($w in $W::TopWindows([uint32]$script:pcsx2Id)) {
        if ($w -eq $script:h) { continue }
        $d = $W::Describe($w)
        $k = "$w"
        if (-not $script:seenDialogs.ContainsKey($k)) {
            $script:seenDialogs[$k] = 0
            Log ("PCSX2 dialog {0} at wall {1:N2} s: closing it" -f $d, (Wall))
            Log ("PCSX2 dialog text: " + (DialogText $w))
        }
        $script:seenDialogs[$k]++
        if ($script:seenDialogs[$k] -le 3) { $W::PostMessage($w, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null }   # WM_CLOSE
        elseif ($script:seenDialogs[$k] -le 6) {
            # Still there: press its default button (Enter / Escape, posted).
            $W::PostMessage($w, 0x100, [IntPtr]27, [IntPtr]0x00010001) | Out-Null
            $W::PostMessage($w, 0x101, [IntPtr]27, [IntPtr]([int64]0xC0010001)) | Out-Null
        }
    }
}
# ---- PINE: the game's VBlank counter as the game clock.
$script:pine = $null
function PineConnect {
    try {
        $c = New-Object Net.Sockets.TcpClient; $c.NoDelay = $true
        $c.ReceiveTimeout = 1000; $c.SendTimeout = 1000
        $c.Connect("127.0.0.1", 28011); $script:pine = $c
    } catch { $script:pine = $null }
}
function PineCall([byte[]]$body) {
    if (-not $script:pine) { PineConnect; if (-not $script:pine) { return $null } }
    try {
        $s = $script:pine.GetStream()
        $req = [BitConverter]::GetBytes([uint32]($body.Length + 4)) + $body
        $s.Write($req, 0, $req.Length)
        $hdr = New-Object byte[] 4; $n = 0
        while ($n -lt 4) { $r = $s.Read($hdr, $n, 4 - $n); if ($r -le 0) { throw "closed" }; $n += $r }
        $size = [BitConverter]::ToUInt32($hdr, 0) - 4
        $rest = New-Object byte[] $size; $n = 0
        while ($n -lt $size) { $r = $s.Read($rest, $n, $size - $n); if ($r -le 0) { throw "closed" }; $n += $r }
        if ($rest[0] -ne 0) { return $null }
        , $rest[1..($rest.Length - 1)]
    } catch { try { $script:pine.Close() } catch {}; $script:pine = $null; $null }
}
function PineRead32([uint32]$a) { $r = PineCall ([byte[]](@(2) + [BitConverter]::GetBytes($a))); if ($r) { [BitConverter]::ToUInt32([byte[]]$r, 0) } else { $null } }
function PineGameId { $r = PineCall ([byte[]]@(0xC)); if ($r -and $r.Length -ge 4) { $n = [BitConverter]::ToUInt32([byte[]]$r, 0); [Text.Encoding]::UTF8.GetString([byte[]]$r, 4, [Math]::Min($n, $r.Length - 4)).TrimEnd([char]0) } else { "" } }
$script:clockOk = $false; $script:lastCount = 0
function GameNow {
    if ($script:clockOk -or ((Wall) -gt 3) -or $State) {
        if (-not $script:clockOk) {
            $id = PineGameId
            if ($id -eq $GameId) { $script:clockOk = $true; Log ("PINE: game {0}, clock = VBlank counter at 0x{1:x}" -f $id, $VsyncAddr) }
        }
        if ($script:clockOk) {
            $c = PineRead32 $VsyncAddr
            if ($c -and $c -ge $script:lastCount -and $c -lt 10000000) { $script:lastCount = $c }
            if ($script:lastCount -gt 0) { return $ClockOffset + $script:lastCount / 59.94 }
        }
    }
    $StateTime + (Wall) * $speed
}
function ClockName { if ($script:lastCount -gt 0) { "vsync $($script:lastCount)" } else { "wall clock" } }

function SleepWatch([double]$ms) {
    $until = (Get-Date).AddMilliseconds($ms)
    while ((Get-Date) -lt $until) {
        CheckUser; CheckDialogs
        $left = ($until - (Get-Date)).TotalMilliseconds
        if ($left -gt 0) { Start-Sleep -Milliseconds ([int][Math]::Min(250, $left)) }
    }
    CheckUser
}
function Press($h, [string]$key) {
    if ($PadKeys.ContainsKey($key.ToUpper())) { $key = $PadKeys[$key.ToUpper()] }
    $vk = [int][System.Windows.Forms.Keys]::Parse([System.Windows.Forms.Keys], $key)
    $sc = [byte]$W::MapVirtualKey($vk, 0)
    # Arrows, Insert/Delete, Home/End, PageUp/Down need the extended-key flag, or they read as keypad keys.
    $ext = if ($vk -in (@(33..40) + @(45, 46))) { 1 } else { 0 }
    $script:ownUntil = (Tick) + 2000
    # A background script may not take the foreground on its own: borrow the input queue
    # of the current foreground window's thread for the switch.
    $fgw = $W::GetForegroundWindow()
    if ($fgw -ne $h) {
        $fp = [uint32]0; $ft = $W::GetWindowThreadProcessId($fgw, [ref]$fp); $me = $W::GetCurrentThreadId()
        $att = ($ft -ne 0 -and $ft -ne $me) -and $W::AttachThreadInput($me, $ft, $true)
        $W::BringWindowToTop($h) | Out-Null; $W::SetForegroundWindow($h) | Out-Null
        if ($att) { $W::AttachThreadInput($me, $ft, $false) | Out-Null }
    }
    Start-Sleep -Milliseconds 100
    if ($W::GetForegroundWindow() -ne $h) { $script:ownUntil = (Tick) + 300; return "$key NOT sent: PCSX2 is not the foreground window" }
    $script:lastFg = $h
    $W::keybd_event([byte]$vk, $sc, $ext, [UIntPtr]::Zero); Start-Sleep -Milliseconds 120
    $W::keybd_event([byte]$vk, $sc, 2 + $ext, [UIntPtr]::Zero)
    $script:ownUntil = (Tick) + 300
    "$key sent"
}
function PostPress($h, [string]$key) {
    # "R1&START": a chord, all keys down together, then all up.
    $parts = @($key -split '&' | ForEach-Object {
        $k = $_.Trim(); if ($PadKeys.ContainsKey($k.ToUpper())) { $PadKeys[$k.ToUpper()] } else { $k } })
    $msgs = New-Object System.Collections.ArrayList
    foreach ($k in $parts) {
        $vk = [int][System.Windows.Forms.Keys]::Parse([System.Windows.Forms.Keys], $k)
        $sc = [int]$W::MapVirtualKey($vk, 0)
        $ext = if ($vk -in (@(33..40) + @(45, 46))) { 1 } else { 0 }
        $lDown = 1 -bor ($sc -shl 16) -bor ($ext -shl 24)
        [void]$msgs.Add(@($vk, $lDown, ([int64]$lDown -bor 0xC0000000L)))
    }
    $tree = $W::WindowTree($h)
    if (-not $script:treeLogged) {
        $script:treeLogged = $true
        Log ("PostKeys targets: " + (($tree | ForEach-Object { $W::Describe($_) }) -join ", "))
    }
    foreach ($m in $msgs) { foreach ($hw in $tree) { $W::PostMessage($hw, 0x100, [IntPtr]$m[0], [IntPtr]$m[1]) | Out-Null } }   # WM_KEYDOWN
    Start-Sleep -Milliseconds 150
    foreach ($m in $msgs) { foreach ($hw in $tree) { $W::PostMessage($hw, 0x101, [IntPtr]$m[0], [IntPtr]$m[2]) | Out-Null } }   # WM_KEYUP
    "$($parts -join '&') posted"
}
function WaitIdleThenPress($h, [string]$key) {
    if ($PostKeys) { return PostPress $h $key }
    if ((UserIdleMs) -lt 10000) {
        while ((UserIdleMs) -lt 10000 -and -not $proc.HasExited) { SleepWatch 250 }
        Log ("Wyn idle 10 s at wall {0:N2} s: resuming" -f (Wall))
    }
    Press $h $key
}
function Shot($h, [string]$file) {
    $note = ""
    if ($W::IsIconic($h)) {
        # A minimised PCSX2 renders nothing and was seen to fall behind its speed target.
        $W::ShowWindow($h, 4) | Out-Null; Start-Sleep -Milliseconds 300   # SW_SHOWNOACTIVATE
        $note = ", window was minimised: restored"
    }
    $r = New-Object Native.Win32Pcsx2c+RECT
    $W::GetWindowRect($h, [ref]$r) | Out-Null
    $wd = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
    $bmp = New-Object System.Drawing.Bitmap $wd, $ht
    $g = [System.Drawing.Graphics]::FromImage($bmp)
    $hdc = $g.GetHdc()
    $ok = $W::PrintWindow($h, $hdc, 2)   # PW_RENDERFULLCONTENT: works when covered, includes D3D content
    $g.ReleaseHdc($hdc)
    $bmp.Save($file, [System.Drawing.Imaging.ImageFormat]::Png); $g.Dispose(); $bmp.Dispose()
    "$wd x $ht" + $(if ($ok) { "" } else { ", PrintWindow failed" }) + $note
}

# Events: captures and key presses, in game-time order.
$events = @()
foreach ($t in $secs) { $events += [pscustomobject]@{ T = [double]$t; Kind = "shot"; Arg = "" } }
foreach ($k in ($Keys -split "," | Where-Object { $_ -match ":" })) {
    $p = $k.Split(":"); $events += [pscustomobject]@{ T = [double]$p[0]; Kind = "key"; Arg = $p[1].Trim() }
}
$events = @($events | Sort-Object T, Kind)
$end = [Math]::Max($Seconds, ($events | Measure-Object T -Maximum).Maximum + 2)

if (-not (Enter-Pcsx2Lock -Owner "pcsx2-capture $Name" -WaitSeconds 1800)) { "could not get local\pcsx2.lock"; exit 1 }
$proc = $null
try {
    if (Get-Process pcsx2-qt -ErrorAction SilentlyContinue) { Log "another PCSX2 is running (not ours); close it first"; exit 1 }
    $mode = if ($Unlimited) { "unlimited until game $($events[0].T - 3) s, then 1x" } elseif ($Turbo) { "turbo x$speed" } else { "real time" }
    Log "start set=$Name times=$($secs -join ',') keys=$Keys end=${end}s $mode, datapath $dataPath, clock offset $ClockOffset$(if ($State) { ", state $State (game $StateTime s)" })"
    $emulog = Join-Path $out "emulog.txt"
    $pargs = @("-datapath", "`"$dataPath`"", "-batch", "-fastboot", "-nofullscreen", "-logfile", "`"$emulog`"")
    if ($Unlimited) { $pargs += "-unlimited" } elseif ($Turbo) { $pargs += "-turbo" }
    if ($State) { $pargs += @("-statefile", "`"$State`"") }
    $script:startTick = Tick
    $script:launch = Get-Date
    # Shown without activation (the key sender below takes the foreground only while Wyn is idle).
    $proc = Start-Quiet $exe -ArgumentList ($pargs + @("--", $iso)) -Window NoActivate -FocusGuardSeconds 20
    if (-not $Sound) { $null = Start-Quiet (Join-Path $PSHOME powershell.exe) -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", "$PSScriptRoot\appaudio.ps1", "-Id", "$($proc.Id)", "-Mute", "-WaitSeconds", "60" }
    $script:pcsx2Id = $proc.Id
    $h = [IntPtr]::Zero
    while ($h -eq [IntPtr]::Zero -and (Wall) -lt 30) {
        Start-Sleep -Milliseconds 200; $proc.Refresh(); $h = $proc.MainWindowHandle
    }
    if ($h -eq [IntPtr]::Zero) { Log "no PCSX2 window after 30 s"; exit 1 }
    $script:h = $h
    Log ("window after {0:N1} s" -f (Wall))

    $limited = -not $Unlimited
    $limitKey = HotkeyName "ToggleFrameLimit" "F4"
    foreach ($e in $events) {
        # Wait for the event's game time, polling the clock (often while unlimited).
        while (-not $proc.HasExited) {
            $g = GameNow
            if (-not $limited -and $g -ge $e.T - 3) {
                $res = WaitIdleThenPress $h $limitKey
                $limited = $res -match "sent$"
                Log ("frame limiter on ({0}) at wall {1:N2} s, game {2:N2} s ({3}){4}" -f $res, (Wall), (GameNow), (ClockName), $(if ($limited) { "" } else { ": still unlimited, timings SUSPECT" }))
                $limited = $true   # don't retry: keys may not reach PCSX2 at all
                continue
            }
            $left = $e.T - $g
            if ($left -le 0) { break }
            $ms = if ($limited) { [Math]::Min(250, $left * 1000 / $speed) } else { 20 }
            CheckUser; CheckDialogs
            Start-Sleep -Milliseconds ([int][Math]::Max(5, $ms))
        }
        if ($proc.HasExited) { Log "PCSX2 exited early"; break }
        if ($e.Kind -eq "key") { $res = WaitIdleThenPress $h $e.Arg }
        else { $f = Join-Path $out ("pcsx2_{0}s.png" -f $e.T); $res = (Split-Path -Leaf $f) + " (" + (Shot $h $f) + ")" }
        $sus = if ($script:userSince -or (UserIdleMs) -lt 10000) { " SUSPECT (Wyn active)" } else { "" }
        $script:userSince = $false
        Log ("{0} {1}s at wall {2:N2} s, game {3:N2} s ({4}): {5}{6}" -f $e.Kind, $e.T, (Wall), (GameNow), (ClockName), $res, $sus)
    }
    while (-not $proc.HasExited -and (GameNow) -lt $end) { SleepWatch 250 }
    if (-not $proc.HasExited) {
        Log ("end at wall {0:N2} s, game {1:N2} s ({2})" -f (Wall), (GameNow), (ClockName))
        # Graceful close (the private config has no "Confirm Shutdown" prompt); dialogs are closed.
        $W::PostMessage($h, 0x10, [IntPtr]::Zero, [IntPtr]::Zero) | Out-Null
        for ($i = 0; $i -lt 30 -and -not $proc.HasExited; $i++) { Start-Sleep -Milliseconds 500; CheckDialogs }
        if ($proc.HasExited) { Log "PCSX2 closed" } else { Log "PCSX2 did not close; killing it" }
    }
} finally {
    if ($script:pine) { try { $script:pine.Close() } catch {} }
    if ($proc -and -not $proc.HasExited) { $proc.Kill(); $proc.WaitForExit(5000) | Out-Null }
    Exit-Pcsx2Lock | Out-Null
}
Log "done"
