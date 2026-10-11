# Captures the game window (title starting with "PS2-Recomp") to a PNG, after moving it to
# the top-left of the primary screen so it is fully visible. Only the window is captured.
#     powershell -File tools\screenshot.ps1 -Out local\runs\run13-40s.png [-HideOverlay]
# -HideOverlay presses F1 in the window (the runtime debugger's toggle): use it once per run.
param([string]$Out = "local/runs/shot.png", [switch]$HideOverlay)
Add-Type -AssemblyName System.Drawing
$sig = @'
[DllImport("user32.dll")] public static extern bool MoveWindow(IntPtr h, int x, int y, int w, int cy, bool repaint);
[DllImport("user32.dll")] public static extern bool GetWindowRect(IntPtr h, out RECT r);
[DllImport("user32.dll")] public static extern bool SetForegroundWindow(IntPtr h);
[DllImport("user32.dll")] public static extern bool SetProcessDPIAware();
[DllImport("user32.dll")] public static extern void keybd_event(byte vk, byte scan, uint flags, UIntPtr extra);
public struct RECT { public int Left, Top, Right, Bottom; }
'@
if (-not ("Win32Shot" -as [type])) { Add-Type -MemberDefinition $sig -Name Win32Shot -Namespace Native }
[Native.Win32Shot]::SetProcessDPIAware() | Out-Null
$p = Get-Process ps2EntryRunner -ErrorAction SilentlyContinue | Where-Object { $_.MainWindowHandle -ne 0 } | Select-Object -First 1
if (-not $p) { "no game window"; exit 1 }
$h = $p.MainWindowHandle
$r = New-Object Native.Win32Shot+RECT
[Native.Win32Shot]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
[Native.Win32Shot]::MoveWindow($h, 0, 0, $w, $ht, $true) | Out-Null
[Native.Win32Shot]::SetForegroundWindow($h) | Out-Null
if ($HideOverlay) { [Native.Win32Shot]::keybd_event(0x70, 0, 0, [UIntPtr]::Zero); Start-Sleep -Milliseconds 50; [Native.Win32Shot]::keybd_event(0x70, 0, 2, [UIntPtr]::Zero) }
Start-Sleep -Milliseconds 600
[Native.Win32Shot]::GetWindowRect($h, [ref]$r) | Out-Null
$w = $r.Right - $r.Left; $ht = $r.Bottom - $r.Top
$bmp = New-Object System.Drawing.Bitmap $w, $ht
$g = [System.Drawing.Graphics]::FromImage($bmp)
$g.CopyFromScreen($r.Left, $r.Top, 0, 0, (New-Object System.Drawing.Size $w, $ht))
$full = [System.IO.Path]::GetFullPath($Out)
$bmp.Save($full); $g.Dispose(); $bmp.Dispose()
"saved $full ($w x $ht)"
