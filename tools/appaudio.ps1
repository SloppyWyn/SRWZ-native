# Per-process audio control through the Windows Core Audio COM APIs (inline C#, nothing installed).
#   -List                                   render devices (name, state, default) and audio sessions
#   -ProcessName pcsx2-qt -Mute [-WaitSeconds 30]   mute that process's sessions on every render device
#   -ProcessName pcsx2-qt -Unmute           undo
#   -ProcessName X -Device "<name part>"    route X's output to another ACTIVE, non-default render
#                                           device (Windows' per-app device preference, the
#                                           undocumented IAudioPolicyConfigFactory EarTrumpet uses)
#   -ProcessName X -ResetDevice             back to the system default
#   -Id <pid> works in place of -ProcessName. -SelfTest mutes a silent child process and reads it back.
# A session exists only once the process has opened an audio stream; -WaitSeconds keeps retrying.
# Windows remembers per-app mute: run -Unmute afterwards if PCSX2 should have sound next time.
param([string]$ProcessName, [int[]]$Id, [switch]$Mute, [switch]$Unmute, [int]$WaitSeconds = 0,
      [switch]$List, [string]$Device, [switch]$ResetDevice, [switch]$SelfTest)
$ErrorActionPreference = "Stop"

if (-not ("AppAudio.Core" -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.Runtime.InteropServices;
namespace AppAudio {
  [StructLayout(LayoutKind.Sequential)] public struct PropKey { public Guid fmtid; public int pid; }
  [StructLayout(LayoutKind.Sequential)] public struct PropVar { public ushort vt; public ushort r1, r2, r3; public IntPtr p; public IntPtr p2; }

  [ComImport, Guid("A95664D2-9614-4F35-A746-DE8DB63617E6"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceEnumerator {
    int EnumAudioEndpoints(int flow, int stateMask, out IMMDeviceCollection devices);
    [PreserveSig] int GetDefaultAudioEndpoint(int flow, int role, out IMMDevice device);
  }
  [ComImport, Guid("0BD7A1BE-7A1A-44DB-8397-CC5392387B5E"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDeviceCollection { int GetCount(out int n); int Item(int i, out IMMDevice d); }
  [ComImport, Guid("D666063F-1587-4E43-81F1-B948E807363F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IMMDevice {
    int Activate(ref Guid iid, int ctx, IntPtr p, [MarshalAs(UnmanagedType.IUnknown)] out object o);
    int OpenPropertyStore(int access, out IPropertyStore ps);
    int GetId([MarshalAs(UnmanagedType.LPWStr)] out string id);
    int GetState(out int state);
  }
  [ComImport, Guid("886d8eeb-8cf2-4446-8d02-cdba1dbdcf99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IPropertyStore {
    int GetCount(out int n); int GetAt(int i, out PropKey k); int GetValue(ref PropKey k, out PropVar v);
  }
  [ComImport, Guid("77AA99A0-1BD6-484F-8BC7-2C654C9A9B6F"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionManager2 {
    int GetAudioSessionControl(IntPtr a, int b, out IntPtr c);
    int GetSimpleAudioVolume(IntPtr a, int b, out IntPtr c);
    int GetSessionEnumerator(out IAudioSessionEnumerator e);
  }
  [ComImport, Guid("E2F5BB11-0570-40CA-ACDD-3AA01277DEE8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionEnumerator { int GetCount(out int n); int GetSession(int i, out IAudioSessionControl2 s); }
  [ComImport, Guid("bfb7ff88-7239-4fc9-8fa2-07c950be9c6d"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioSessionControl2 {
    int GetState(out int state);
    int GetDisplayName([MarshalAs(UnmanagedType.LPWStr)] out string s);
    int SetDisplayName(string s, ref Guid g);
    int GetIconPath([MarshalAs(UnmanagedType.LPWStr)] out string s);
    int SetIconPath(string s, ref Guid g);
    int GetGroupingParam(out Guid g);
    int SetGroupingParam(ref Guid g, ref Guid ctx);
    int RegisterAudioSessionNotification(IntPtr n);
    int UnregisterAudioSessionNotification(IntPtr n);
    int GetSessionIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string s);
    int GetSessionInstanceIdentifier([MarshalAs(UnmanagedType.LPWStr)] out string s);
    [PreserveSig] int GetProcessId(out uint pid);
    [PreserveSig] int IsSystemSoundsSession();
  }
  [ComImport, Guid("87CE5498-68D6-44E5-9215-6DA47EF883D8"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface ISimpleAudioVolume {
    int SetMasterVolume(float v, ref Guid ctx); int GetMasterVolume(out float v);
    int SetMute([MarshalAs(UnmanagedType.Bool)] bool m, ref Guid ctx); int GetMute([MarshalAs(UnmanagedType.Bool)] out bool m);
  }
  // Undocumented (Windows 10 21H2+ / 11), as used by EarTrumpet and SoundVolumeView.
  [ComImport, Guid("ab3d4648-e242-459f-b02f-541c70306324"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
  interface IAudioPolicyConfigFactory {
    void GetIids(); void GetRuntimeClassName(); void GetTrustLevel();   // IInspectable
    void u0(); void u1(); void u2(); void u3(); void u4(); void u5(); void u6(); void u7(); void u8(); void u9();
    void u10(); void u11(); void u12(); void u13(); void u14(); void u15(); void u16(); void u17(); void u18();
    [PreserveSig] int SetPersistedDefaultAudioEndpoint(uint pid, int flow, int role, IntPtr deviceId);
    [PreserveSig] int GetPersistedDefaultAudioEndpoint(uint pid, int flow, int role, out IntPtr deviceId);
    [PreserveSig] int ClearAllPersistedApplicationDefaultEndpoints();
  }
  [ComImport, Guid("BCDE0395-E52F-467C-8E3D-C4579291692E")] class MMDeviceEnumeratorCo { }

  public class Dev { public string Name, Id; public int State; public bool IsDefault, IsDefaultComms; }
  public class Sess { public string Device; public uint Pid; public int State; public bool Muted, System; public float Volume; public string DisplayName; }

  public static class Core {
    const int eRender = 0, eConsole = 0, eMultimedia = 1, eCommunications = 2;
    const string RenderIface = "{e6327cad-dcec-4949-ae8a-991e976a79d2}";
    static IMMDeviceEnumerator En() { return (IMMDeviceEnumerator)new MMDeviceEnumeratorCo(); }
    static string DefaultId(IMMDeviceEnumerator e, int role) {
      IMMDevice d; if (e.GetDefaultAudioEndpoint(eRender, role, out d) != 0 || d == null) return null;
      string id; d.GetId(out id); return id;
    }
    static string Name(IMMDevice d) {
      try { return Name1(d); } catch (COMException) { return "(name unavailable)"; }   // e.g. removed devices
    }
    static string Name1(IMMDevice d) {
      IPropertyStore ps; d.OpenPropertyStore(0, out ps);
      PropKey k = new PropKey(); k.fmtid = new Guid("a45c254e-df1c-4efd-8020-67d146a850e0"); k.pid = 14;
      PropVar v; ps.GetValue(ref k, out v);
      return v.vt == 31 ? Marshal.PtrToStringUni(v.p) : "?";
    }
    static List<KeyValuePair<Dev, IMMDevice>> Raw(int stateMask) {
      var e = En(); var res = new List<KeyValuePair<Dev, IMMDevice>>();
      string dc = DefaultId(e, eConsole), dm = DefaultId(e, eCommunications);
      IMMDeviceCollection c; e.EnumAudioEndpoints(eRender, stateMask, out c);
      int n; c.GetCount(out n);
      for (int i = 0; i < n; i++) {
        IMMDevice d; c.Item(i, out d);
        var x = new Dev(); d.GetId(out x.Id); d.GetState(out x.State); x.Name = Name(d);
        x.IsDefault = x.Id == dc; x.IsDefaultComms = x.Id == dm;
        res.Add(new KeyValuePair<Dev, IMMDevice>(x, d));
      }
      return res;
    }
    public static Dev[] Devices() { var l = new List<Dev>(); foreach (var p in Raw(0xF)) l.Add(p.Key); return l.ToArray(); }

    // Visits every session on every active render device; pids == null means all. mute: null = read only.
    public static Sess[] Sessions(uint[] pids, object mute) {
      var res = new List<Sess>(); Guid ctx = Guid.Empty;
      foreach (var p in Raw(1)) {
        Guid iid = typeof(IAudioSessionManager2).GUID; object o;
        p.Value.Activate(ref iid, 23, IntPtr.Zero, out o);
        IAudioSessionEnumerator se; ((IAudioSessionManager2)o).GetSessionEnumerator(out se);
        int n; se.GetCount(out n);
        for (int i = 0; i < n; i++) {
          IAudioSessionControl2 s; se.GetSession(i, out s);
          uint pid; s.GetProcessId(out pid);
          if (pids != null && Array.IndexOf(pids, pid) < 0) continue;
          var vol = (ISimpleAudioVolume)s;
          if (mute != null) vol.SetMute((bool)mute, ref ctx);
          var x = new Sess(); x.Device = p.Key.Name; x.Pid = pid; s.GetState(out x.State);
          s.GetDisplayName(out x.DisplayName); x.System = s.IsSystemSoundsSession() == 0;
          vol.GetMute(out x.Muted); vol.GetMasterVolume(out x.Volume);
          res.Add(x);
        }
      }
      return res.ToArray();
    }

    [DllImport("combase.dll")] static extern int WindowsCreateString([MarshalAs(UnmanagedType.LPWStr)] string s, int len, out IntPtr h);
    [DllImport("combase.dll")] static extern int WindowsDeleteString(IntPtr h);
    [DllImport("combase.dll")] static extern IntPtr WindowsGetStringRawBuffer(IntPtr h, out int len);
    [DllImport("combase.dll")] static extern int RoGetActivationFactory(IntPtr cls, ref Guid iid, out IntPtr f);
    static IAudioPolicyConfigFactory Policy() {
      const string cls = "Windows.Media.Internal.AudioPolicyConfig";
      IntPtr h; WindowsCreateString(cls, cls.Length, out h);
      Guid iid = typeof(IAudioPolicyConfigFactory).GUID; IntPtr f;
      int hr = RoGetActivationFactory(h, ref iid, out f); WindowsDeleteString(h);
      if (hr != 0) throw new Exception("IAudioPolicyConfigFactory not available on this Windows build (hr=0x" + hr.ToString("X8") + ")");
      var o = (IAudioPolicyConfigFactory)Marshal.GetObjectForIUnknown(f); Marshal.Release(f); return o;
    }
    // deviceId null = reset to the system default. Sets the console and multimedia roles, as Windows' UI does.
    public static void Route(uint pid, string deviceId) {
      var pf = Policy();
      IntPtr h = IntPtr.Zero;
      if (!String.IsNullOrEmpty(deviceId)) {   // PowerShell passes $null as ""
        string full = @"\\?\SWD#MMDEVAPI#" + deviceId + "#" + RenderIface;
        WindowsCreateString(full, full.Length, out h);
      }
      try {
        foreach (int role in new[] { eMultimedia, eConsole }) {
          int hr = pf.SetPersistedDefaultAudioEndpoint(pid, eRender, role, h);
          if (hr != 0) throw new Exception("SetPersistedDefaultAudioEndpoint role " + role + " failed (hr=0x" + hr.ToString("X8") + ")");
        }
      } finally { if (h != IntPtr.Zero) WindowsDeleteString(h); }
    }
    // Returns the routed device id for that process, or "" when it follows the default.
    public static string RoutedTo(uint pid) {
      IntPtr h; int hr = Policy().GetPersistedDefaultAudioEndpoint(pid, eRender, eMultimedia, out h);
      if (hr != 0 || h == IntPtr.Zero) return "";
      int len; string s = Marshal.PtrToStringUni(WindowsGetStringRawBuffer(h, out len), len); WindowsDeleteString(h);
      int a = s.IndexOf("{0.0.0"); if (a < 0) return s;
      int b = s.IndexOf('#', a); return b < 0 ? s.Substring(a) : s.Substring(a, b - a);
    }
  }
}
'@
}

$stateNames = @{ 1 = "ACTIVE"; 2 = "DISABLED"; 4 = "NOTPRESENT"; 8 = "UNPLUGGED" }
$sessStates = @{ 0 = "inactive"; 1 = "active"; 2 = "expired" }

function Show-Sessions($s) {
    $s | ForEach-Object {
        $pn = if ($_.System) { "(system sounds)" } else { (Get-Process -Id $_.Pid -ErrorAction SilentlyContinue).ProcessName }
        [pscustomobject]@{ Device = $_.Device; Pid = $_.Pid; Process = $pn; State = $sessStates[$_.State]
                           Muted = $_.Muted; Volume = [math]::Round($_.Volume, 2) }
    } | Format-Table -AutoSize | Out-String -Width 200
}

function Get-Pids {
    $p = @()
    if ($Id) { $p += $Id }
    if ($ProcessName) { $p += @(Get-Process -Name $ProcessName -ErrorAction SilentlyContinue | ForEach-Object Id) }
    [uint32[]]$p
}

function Set-Mute([bool]$m, [int]$wait) {
    $deadline = (Get-Date).AddSeconds($wait)
    while ($true) {
        $pids = Get-Pids
        $s = if ($pids) { [AppAudio.Core]::Sessions($pids, $m) } else { @() }
        if ($s.Count -gt 0) { return $s }
        if ((Get-Date) -ge $deadline) { return @() }
        Start-Sleep -Milliseconds 100
    }
}

if ($List) {
    "Render devices:"
    $all = [AppAudio.Core]::Devices()
    $all | Where-Object { $_.State -ne 4 } | ForEach-Object {
        [pscustomobject]@{ Name = $_.Name; State = $stateNames[$_.State]
            Default = (@(if ($_.IsDefault) { "default" }; if ($_.IsDefaultComms) { "comms" }) -join ",")
            Id = $_.Id }
    } | Format-Table -AutoSize | Out-String -Width 250
    "($(@($all | Where-Object { $_.State -eq 4 }).Count) NOTPRESENT devices not shown)"
    "Audio sessions (active render devices):"
    Show-Sessions ([AppAudio.Core]::Sessions($null, $null))
    exit 0
}

if ($SelfTest) {
    $dir = Join-Path $env:TEMP "appaudio-selftest"; New-Item -ItemType Directory -Force $dir | Out-Null
    $wav = Join-Path $dir "silence.wav"
    # 4 s of 16-bit mono silence at 22050 Hz: opens an audio session without making any sound.
    $n = 22050 * 4 * 2; $ms = New-Object IO.MemoryStream; $w = New-Object IO.BinaryWriter $ms
    $w.Write([Text.Encoding]::ASCII.GetBytes("RIFF")); $w.Write([int](36 + $n)); $w.Write([Text.Encoding]::ASCII.GetBytes("WAVEfmt "))
    $w.Write([int]16); $w.Write([int16]1); $w.Write([int16]1); $w.Write([int]22050); $w.Write([int]44100)
    $w.Write([int16]2); $w.Write([int16]16); $w.Write([Text.Encoding]::ASCII.GetBytes("data")); $w.Write([int]$n)
    $w.Write((New-Object byte[] $n)); $w.Flush(); [IO.File]::WriteAllBytes($wav, $ms.ToArray())
    $ps = Join-Path $PSHOME "powershell.exe"
    . (Join-Path $PSScriptRoot "launch.ps1")  # Start-Quiet: no console window
    $child = Start-Quiet $ps -ArgumentList "-NoProfile", "-Command", "(New-Object Media.SoundPlayer '$wav').PlaySync()"
    $ok = $false
    try {
        $Id = @($child.Id); $ProcessName = $null
        $s = Set-Mute $true 10
        if ($s.Count -eq 0) { "FAIL: no audio session for child pid $($child.Id) within 10 s" }
        else {
            Start-Sleep -Milliseconds 300
            $back = [AppAudio.Core]::Sessions([uint32[]]@($child.Id), $null)
            Show-Sessions $back
            $ok = ($back.Count -gt 0) -and -not ($back | Where-Object { -not $_.Muted })
            [void][AppAudio.Core]::Sessions([uint32[]]@($child.Id), $false)   # leave powershell.exe unmuted
            $un = [AppAudio.Core]::Sessions([uint32[]]@($child.Id), $null)
            $ok = $ok -and -not ($un | Where-Object { $_.Muted })
            if ($ok) { "PASS: child pid $($child.Id) muted on $($back.Count) session(s), read back muted, then unmuted" }
            else { "FAIL: mute did not read back as expected" }
        }
    } finally { if (-not $child.HasExited) { $child.Kill() } }
    if ($ok) { exit 0 } else { exit 1 }
}

if (-not $ProcessName -and -not $Id) { "give -ProcessName or -Id (or -List / -SelfTest)"; exit 2 }

if ($Mute -or $Unmute) {
    $s = Set-Mute ([bool]$Mute) $WaitSeconds
    if ($s.Count -eq 0) { "no audio session for $ProcessName$Id (not running, or not playing yet; try -WaitSeconds)"; exit 1 }
    Show-Sessions $s
    exit 0
}

if ($Device -or $ResetDevice) {
    $pids = Get-Pids
    if (-not $pids) { "process $ProcessName$Id is not running"; exit 1 }
    if ($ResetDevice) {
        foreach ($p in $pids) { [AppAudio.Core]::Route($p, $null); "pid ${p}: routed to '$([AppAudio.Core]::RoutedTo($p))' (empty = system default)" }
        exit 0
    }
    $devs = @([AppAudio.Core]::Devices() | Where-Object { $_.Name -like "*$Device*" })
    $act = @($devs | Where-Object { $_.State -eq 1 })
    if ($devs.Count -gt 0 -and $act.Count -eq 0) { "refused: no ACTIVE render device matches '$Device'"; exit 1 }
    if ($act.Count -ne 1) { "'$Device' matches $($act.Count) active render devices; need exactly one (see -List)"; exit 1 }
    $d = $act[0]
    if ($d.IsDefault -or $d.IsDefaultComms) { "refused: '$($d.Name)' is a default device"; exit 1 }
    foreach ($p in $pids) {
        [AppAudio.Core]::Route($p, $d.Id)
        $r = [AppAudio.Core]::RoutedTo($p)
        if ($r -eq $d.Id) { "pid ${p}: routed to '$($d.Name)' (undo with -ResetDevice)" } else { "pid ${p}: route did not read back (got '$r')"; exit 1 }
    }
    exit 0
}

"nothing to do: give -Mute, -Unmute, -Device or -ResetDevice"; exit 2
