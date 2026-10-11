# The one way our scripts and agents start processes: no console window, no focus steal.
# On Windows 11 every console process started without an inherited console (Start-Process,
# cmd /c start, a console exe such as ps2EntryRunner.exe, cmake/MSBuild/cl, python, powershell)
# is routed to Windows Terminal and opens a window in front of Wyn. This starts children with
# CreateProcess + CREATE_NO_WINDOW (a console of their own that has no window; their children
# inherit it, so nothing below them opens one either), stdout/stderr straight into files (or
# captured), stdin NUL, only those handles inherited. A GUI window the child opens itself (the
# game in windowed mode, PCSX2) gets SW_SHOWNOACTIVATE (default) or SW_SHOWMINNOACTIVE: shown
# without activation, never brought to the foreground by us.
# PowerShell: dot-source it, then
#     . "$PSScriptRoot\launch.ps1"
#     $p = Start-Quiet $exe -ArgumentList a, "b c" [-RawArguments "/d /s /c ""..."""] [-WorkingDirectory d] [-StdOut f] [-StdErr f]
#              [-Append] [-Env @{K='V'}] [-Window NoActivate|Minimized|Hidden] [-Position 0,0]
#              [-FocusGuardSeconds 30]   (a GUI child that grabs the foreground gets it taken back)
#         -> System.Diagnostics.Process (Id, HasExited, WaitForExit(ms), ExitCode, CloseMainWindow)
#     Wait-Quiet $p [-TimeoutSeconds N] [-Kill]   -> exit code, or $null on timeout
#     $lines = Invoke-Quiet python @("x.py", "a", "b") [-WorkingDirectory d] [-MergeErr] [-Echo] [-TimeoutSeconds N]
#         runs to the end like `& python x.py a b`: stdout lines to the pipeline (-Echo also
#         prints them live), stderr to the host (-MergeErr: into stdout), $LASTEXITCODE set.
#     Get-VisibleWindows   visible top-level windows (Hwnd, Pid, Process, Title); Get-ForegroundHwnd
# Command line (from a .bat or another shell; stdout/stderr pass through, exit code = the child's):
#     powershell -NoProfile -ExecutionPolicy Bypass -File tools\launch.ps1 [-NoWait] [-TimeoutSeconds N]
#         [-Cwd d] [-StdOut f] [-StdErr f] [-Window ...] -- <exe> <args...>
#     powershell -File tools\launch.ps1 -SelfTest
# Never use Start-Process, cmd /c start, or `&` on a console exe from a shell that has no console.
# No param block: dot-sourcing would overwrite the caller's variables (-SelfTest...); the
# command line is parsed from $args at the end.

if (-not ('SrwzLaunch.Native' -as [type])) {
Add-Type -TypeDefinition @'
using System;
using System.Collections.Generic;
using System.ComponentModel;
using System.Runtime.InteropServices;
using System.Text;

namespace SrwzLaunch {
public class Started { public int Pid; public IntPtr Process; public IntPtr Thread; }
public class Win { public IntPtr Hwnd; public int Pid; public string Title; public string Class; }

public static class Native {
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct STARTUPINFO {
        public int cb; public string lpReserved; public string lpDesktop; public string lpTitle;
        public int dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
        public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
    }
    [StructLayout(LayoutKind.Sequential)]
    struct STARTUPINFOEX { public STARTUPINFO StartupInfo; public IntPtr lpAttributeList; }
    [StructLayout(LayoutKind.Sequential)]
    struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public int dwProcessId, dwThreadId; }
    [StructLayout(LayoutKind.Sequential)]
    struct SECURITY_ATTRIBUTES { public int nLength; public IntPtr lpSecurityDescriptor; public bool bInheritHandle; }

    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern bool CreateProcessW(string app, StringBuilder cmd, IntPtr pa, IntPtr ta, bool inherit,
        uint flags, IntPtr env, string cwd, ref STARTUPINFOEX si, out PROCESS_INFORMATION pi);
    [DllImport("kernel32.dll", SetLastError = true, CharSet = CharSet.Unicode)]
    static extern IntPtr CreateFileW(string name, uint access, uint share, ref SECURITY_ATTRIBUTES sa,
        uint disp, uint flags, IntPtr tmpl);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool InitializeProcThreadAttributeList(IntPtr list, int count, int flags, ref IntPtr size);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool UpdateProcThreadAttribute(IntPtr list, uint flags, IntPtr attr, IntPtr value,
        IntPtr size, IntPtr prev, IntPtr retSize);
    [DllImport("kernel32.dll")] static extern void DeleteProcThreadAttributeList(IntPtr list);
    [DllImport("kernel32.dll", SetLastError = true)] public static extern bool CloseHandle(IntPtr h);
    [DllImport("kernel32.dll")] static extern uint ResumeThread(IntPtr h);
    [DllImport("kernel32.dll")] static extern IntPtr GetStdHandle(int n);
    [DllImport("kernel32.dll")] static extern IntPtr GetCurrentProcess();
    [DllImport("kernel32.dll")] static extern uint GetFileType(IntPtr h);
    [DllImport("kernel32.dll", SetLastError = true)]
    static extern bool DuplicateHandle(IntPtr sp, IntPtr s, IntPtr tp, out IntPtr t, uint access, bool inherit, uint opts);
    [DllImport("kernel32.dll")] static extern uint GetConsoleProcessList(uint[] list, uint count);
    [DllImport("kernel32.dll")] static extern bool TerminateProcess(IntPtr h, uint code);

    const uint CREATE_SUSPENDED = 0x4, CREATE_UNICODE_ENVIRONMENT = 0x400,
        EXTENDED_STARTUPINFO_PRESENT = 0x80000, CREATE_NO_WINDOW = 0x08000000;
    const int STARTF_USESHOWWINDOW = 0x1, STARTF_USEPOSITION = 0x4, STARTF_USESTDHANDLES = 0x100;
    static readonly IntPtr PROC_THREAD_ATTRIBUTE_HANDLE_LIST = (IntPtr)0x20002;

    static IntPtr OpenFile(string path, bool write, bool append) {
        var sa = new SECURITY_ATTRIBUTES { nLength = Marshal.SizeOf(typeof(SECURITY_ATTRIBUTES)), bInheritHandle = true };
        // GENERIC_READ 0x80000000, GENERIC_WRITE 0x40000000, FILE_APPEND_DATA 0x4 (+SYNCHRONIZE);
        // share read|write|delete so a script can read the log while the child writes it.
        uint access = !write ? 0x80000000u : (append ? 0x00100004u : 0x40000000u);
        uint disp = !write ? 3u /*OPEN_EXISTING*/ : (append ? 4u /*OPEN_ALWAYS*/ : 2u /*CREATE_ALWAYS*/);
        IntPtr h = CreateFileW(path, access, 7, ref sa, disp, 0x80, IntPtr.Zero);
        if (h == (IntPtr)(-1)) throw new Win32Exception(Marshal.GetLastWin32Error(), "open " + path);
        return h;
    }

    static IntPtr Inheritable(IntPtr h) {
        IntPtr d;
        if (h == IntPtr.Zero || h == (IntPtr)(-1)) return IntPtr.Zero;
        if (!DuplicateHandle(GetCurrentProcess(), h, GetCurrentProcess(), out d, 0, true, 2 /*SAME_ACCESS*/)) return IntPtr.Zero;
        return d;
    }

    public static bool HasConsole() { return GetConsoleProcessList(new uint[4], 4) > 0; }

    // Creates the process suspended (the caller opens a Process object on it, then calls Resume:
    // a child that exits at once still has a readable exit code). outPath/errPath null = NUL,
    // "" = this process's own stdout/stderr (inheritStd). errPath == outPath = one shared file.
    // show < 0: no STARTF_USESHOWWINDOW. x/y >= 0: STARTF_USEPOSITION.
    public static Started Create(string app, string cmdline, string cwd, string envBlock,
            string outPath, string errPath, bool append, int show, int x, int y, bool shareConsole) {
        var si = new STARTUPINFOEX();
        si.StartupInfo.cb = Marshal.SizeOf(typeof(STARTUPINFOEX));
        si.StartupInfo.dwFlags = STARTF_USESTDHANDLES;
        if (show >= 0) { si.StartupInfo.dwFlags |= STARTF_USESHOWWINDOW; si.StartupInfo.wShowWindow = (short)show; }
        if (x >= 0 && y >= 0) { si.StartupInfo.dwFlags |= STARTF_USEPOSITION; si.StartupInfo.dwX = x; si.StartupInfo.dwY = y; }
        var owned = new List<IntPtr>();
        IntPtr attrs = IntPtr.Zero, listMem = IntPtr.Zero;
        try {
            IntPtr hin = OpenFile("NUL", false, false); owned.Add(hin);
            IntPtr hout, herr;
            if (outPath == null) { hout = OpenFile("NUL", true, false); owned.Add(hout); }
            else if (outPath == "") { hout = Inheritable(GetStdHandle(-11)); if (hout != IntPtr.Zero) owned.Add(hout); }
            else { hout = OpenFile(outPath, true, append); owned.Add(hout); }
            if (errPath != null && errPath == outPath && outPath != "") herr = hout;
            else if (errPath == null) { herr = OpenFile("NUL", true, false); owned.Add(herr); }
            else if (errPath == "") { herr = Inheritable(GetStdHandle(-12)); if (herr != IntPtr.Zero) owned.Add(herr); }
            else { herr = OpenFile(errPath, true, append); owned.Add(herr); }
            si.StartupInfo.hStdInput = hin; si.StartupInfo.hStdOutput = hout; si.StartupInfo.hStdError = herr;

            // Inherit exactly these handles, nothing else of ours (pipes of the calling shell...).
            var list = new List<IntPtr>();
            foreach (var h in new[] { hin, hout, herr })
                if (h != IntPtr.Zero && !list.Contains(h)) list.Add(h);
            IntPtr size = IntPtr.Zero;
            InitializeProcThreadAttributeList(IntPtr.Zero, 1, 0, ref size);
            attrs = Marshal.AllocHGlobal(size);
            if (!InitializeProcThreadAttributeList(attrs, 1, 0, ref size)) throw new Win32Exception();
            listMem = Marshal.AllocHGlobal(IntPtr.Size * Math.Max(1, list.Count));
            for (int i = 0; i < list.Count; i++) Marshal.WriteIntPtr(listMem, i * IntPtr.Size, list[i]);
            if (list.Count > 0 && !UpdateProcThreadAttribute(attrs, 0, PROC_THREAD_ATTRIBUTE_HANDLE_LIST, listMem,
                    (IntPtr)(IntPtr.Size * list.Count), IntPtr.Zero, IntPtr.Zero)) throw new Win32Exception();
            si.lpAttributeList = attrs;

            // shareConsole: run in this process's console (it has one: no window either way);
            // otherwise a console of its own without a window.
            uint flags = CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT | EXTENDED_STARTUPINFO_PRESENT;
            if (!shareConsole) flags |= CREATE_NO_WINDOW;
            IntPtr env = envBlock == null ? IntPtr.Zero : Marshal.StringToHGlobalUni(envBlock);
            PROCESS_INFORMATION pi;
            try {
                if (!CreateProcessW(app, new StringBuilder(cmdline), IntPtr.Zero, IntPtr.Zero, list.Count > 0,
                        flags, env, string.IsNullOrEmpty(cwd) ? null : cwd, ref si, out pi))
                    throw new Win32Exception(Marshal.GetLastWin32Error(), "CreateProcess " + cmdline);
            } finally { if (env != IntPtr.Zero) Marshal.FreeHGlobal(env); }
            return new Started { Pid = pi.dwProcessId, Process = pi.hProcess, Thread = pi.hThread };
        } finally {
            foreach (var h in owned) CloseHandle(h);
            if (attrs != IntPtr.Zero) { DeleteProcThreadAttributeList(attrs); Marshal.FreeHGlobal(attrs); }
            if (listMem != IntPtr.Zero) Marshal.FreeHGlobal(listMem);
        }
    }

    public static void Resume(Started s) {
        ResumeThread(s.Thread); CloseHandle(s.Thread); CloseHandle(s.Process);
    }
    public static void Abort(Started s) {
        TerminateProcess(s.Process, 1); CloseHandle(s.Thread); CloseHandle(s.Process);
    }

    // ---- windows ----
    delegate bool EnumProc(IntPtr h, IntPtr l);
    [DllImport("user32.dll")] static extern bool EnumWindows(EnumProc f, IntPtr l);
    [DllImport("user32.dll")] static extern bool IsWindowVisible(IntPtr h);
    [DllImport("user32.dll")] static extern IntPtr GetWindow(IntPtr h, uint cmd);
    [DllImport("user32.dll")] static extern int GetWindowThreadProcessId(IntPtr h, out int pid);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetWindowTextW(IntPtr h, StringBuilder s, int n);
    [DllImport("user32.dll", CharSet = CharSet.Unicode)] static extern int GetClassNameW(IntPtr h, StringBuilder s, int n);
    [DllImport("dwmapi.dll")] static extern int DwmGetWindowAttribute(IntPtr h, int attr, out int v, int size);
    [DllImport("user32.dll")] public static extern IntPtr GetForegroundWindow();
    [DllImport("user32.dll")] public static extern bool IsIconic(IntPtr h);
    [DllImport("user32.dll")] public static extern bool SetWindowPos(IntPtr h, IntPtr after, int x, int y, int cx, int cy, uint flags);
    [DllImport("user32.dll")] static extern bool SetForegroundWindow(IntPtr h);

    // Focus guard: a GUI child that activates its own window at start-up (raylib/GLFW focuses
    // the game window on show, whatever the show mode) gets the foreground on this machine.
    // A background thread hands the foreground straight back to the window that had it (polled
    // every 15 ms) until 5 s after the child's first window appeared, or ms at most. A window
    // the user switches to meanwhile becomes the one handed back to.
    public class Guard { public volatile int Restored; public volatile bool Done; }
    public static Guard GuardFocus(int pid, int ms) {
        var g = new Guard();
        IntPtr first = GetForegroundWindow();
        var t = new System.Threading.Thread(() => {
            IntPtr prev = first;
            var start = DateTime.UtcNow; DateTime seen = DateTime.MaxValue;
            while ((DateTime.UtcNow - start).TotalMilliseconds < ms && (DateTime.UtcNow - seen).TotalSeconds < 5) {
                IntPtr fg = GetForegroundWindow();
                int owner = 0; if (fg != IntPtr.Zero) GetWindowThreadProcessId(fg, out owner);
                if (seen == DateTime.MaxValue) foreach (var w in Windows()) if (w.Pid == pid) { seen = DateTime.UtcNow; break; }
                if (owner == pid) { if (prev != IntPtr.Zero && SetForegroundWindow(prev)) g.Restored++; }
                else if (fg != IntPtr.Zero) prev = fg;
                System.Threading.Thread.Sleep(15);
            }
            g.Done = true;
        });
        t.IsBackground = true; t.Start();
        return g;
    }

    // ---- watching our own process tree's windows (self-tests) ----
    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    struct PROCESSENTRY32 { public int dwSize, cntUsage, th32ProcessID; public IntPtr th32DefaultHeapID; public int th32ModuleID, cntThreads, th32ParentProcessID, pcPriClassBase, dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 260)] public string szExeFile; }
    [DllImport("kernel32.dll", SetLastError = true)] static extern IntPtr CreateToolhelp32Snapshot(uint flags, int pid);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool Process32FirstW(IntPtr s, ref PROCESSENTRY32 e);
    [DllImport("kernel32.dll", CharSet = CharSet.Unicode)] static extern bool Process32NextW(IntPtr s, ref PROCESSENTRY32 e);

    // Polls every 100 ms: follows the process tree under the root pids (children are added
    // while they live), and records every visible top-level window that was not there at the
    // start and belongs to that tree: owned by one of its processes, or a terminal window
    // (Windows Terminal / conhost) titled with one of its exe names, which is how a console
    // handed to Windows Terminal shows up. Other people's windows are ignored.
    public class TreeWatch {
        public readonly HashSet<int> Pids = new HashSet<int>();
        public readonly HashSet<string> Exes = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        public readonly List<Win> Ours = new List<Win>();
        public volatile bool Stop;
        public void Add(int pid) { lock (Pids) Pids.Add(pid); }
    }
    public static TreeWatch WatchTree(int[] roots) {
        var w = new TreeWatch();
        foreach (var r in roots) w.Add(r);
        var baseline = new HashSet<IntPtr>(); foreach (var x in Windows()) baseline.Add(x.Hwnd);
        var seen = new HashSet<IntPtr>();
        var t = new System.Threading.Thread(() => {
            while (!w.Stop) {
                IntPtr snap = CreateToolhelp32Snapshot(2 /*TH32CS_SNAPPROCESS*/, 0);
                if (snap != (IntPtr)(-1)) {
                    var e = new PROCESSENTRY32(); e.dwSize = Marshal.SizeOf(typeof(PROCESSENTRY32));
                    var all = new List<PROCESSENTRY32>();
                    if (Process32FirstW(snap, ref e)) do { all.Add(e); } while (Process32NextW(snap, ref e));
                    CloseHandle(snap);
                    bool grew = true;
                    while (grew) {
                        grew = false;
                        foreach (var p in all) lock (w.Pids) {
                            if (w.Pids.Contains(p.th32ProcessID)) { w.Exes.Add(p.szExeFile); continue; }
                            if (w.Pids.Contains(p.th32ParentProcessID) && p.th32ParentProcessID != 0) { w.Pids.Add(p.th32ProcessID); grew = true; }
                        }
                    }
                }
                foreach (var x in Windows()) {
                    if (baseline.Contains(x.Hwnd) || seen.Contains(x.Hwnd)) continue;
                    bool ours;
                    lock (w.Pids) ours = w.Pids.Contains(x.Pid);
                    if (!ours && (x.Class == "CASCADIA_HOSTING_WINDOW_CLASS" || x.Class == "ConsoleWindowClass"))
                        foreach (var exe in w.Exes)
                            if (x.Title.EndsWith(exe, StringComparison.OrdinalIgnoreCase) || x.Title.IndexOf("\\" + exe + " ", StringComparison.OrdinalIgnoreCase) >= 0) { ours = true; break; }
                    if (ours) { seen.Add(x.Hwnd); lock (w.Ours) w.Ours.Add(x); }
                }
                System.Threading.Thread.Sleep(100);
            }
        });
        t.IsBackground = true; t.Start();
        return w;
    }

    // Visible, uncloaked, unowned top-level windows: what shows up on the desktop and taskbar.
    public static List<Win> Windows() {
        var r = new List<Win>();
        EnumWindows((h, l) => {
            if (!IsWindowVisible(h) || GetWindow(h, 4 /*GW_OWNER*/) != IntPtr.Zero) return true;
            int cloaked; if (DwmGetWindowAttribute(h, 14 /*DWMWA_CLOAKED*/, out cloaked, 4) == 0 && cloaked != 0) return true;
            int pid; GetWindowThreadProcessId(h, out pid);
            var t = new StringBuilder(512); GetWindowTextW(h, t, 512);
            var c = new StringBuilder(256); GetClassNameW(h, c, 256);
            r.Add(new Win { Hwnd = h, Pid = pid, Title = t.ToString(), Class = c.ToString() });
            return true;
        }, IntPtr.Zero);
        return r;
    }
}
}
'@
}

# Windows command-line quoting of one argument (CommandLineToArgvW rules). An argument that is
# already wrapped in double quotes is taken as quoted.
function ConvertTo-QuietArg([string]$a) {
    if ($a.Length -gt 1 -and $a.StartsWith('"') -and $a.EndsWith('"')) { return $a }
    if ($a -ne '' -and $a -notmatch '[\s"]') { return $a }
    '"' + (($a -replace '(\\*)"', '$1$1\"') -replace '(\\+)$', '$1$1') + '"'
}

function Resolve-QuietExe([string]$FilePath) {
    if ([IO.Path]::IsPathRooted($FilePath) -and (Test-Path -LiteralPath $FilePath)) { return $FilePath }
    if (Test-Path -LiteralPath $FilePath -PathType Leaf) { return (Resolve-Path -LiteralPath $FilePath).Path }
    $c = Get-Command $FilePath -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    if ($c) { return $c.Source }
    throw "launch: not found: $FilePath"
}

$script:QuietShow = @{ NoActivate = 4; Minimized = 7; Hidden = 0; Normal = 1; Default = -1 }

# Starts a process quietly; returns its System.Diagnostics.Process (exit code readable).
# StdOut/StdErr: file paths (created, or appended with -Append); none = NUL; the same path for
# both = one file. -InheritStd: the caller's own stdout/stderr (command-line use). Env: extra
# variables for this child only (on top of this process's environment, which $env: sets).
# -FocusGuardSeconds N: for a child that opens a window (the game, PCSX2): if it takes the
# foreground anyway (GLFW focuses its window on show), hand it straight back, until 5 s after
# its window appeared (N s at most; this script must stay alive meanwhile).
function Start-Quiet {
    param(
        [Parameter(Mandatory = $true, Position = 0)] [string]$FilePath,
        [Parameter(Position = 1)] [string[]]$ArgumentList = @(),
        [string]$RawArguments = "",
        [string]$WorkingDirectory = "",
        [string]$StdOut = $null, [string]$StdErr = $null, [switch]$Append, [switch]$InheritStd,
        [hashtable]$Env = $null,
        [ValidateSet('NoActivate', 'Minimized', 'Hidden', 'Normal', 'Default')] [string]$Window = 'NoActivate',
        [int[]]$Position = @(),
        [int]$FocusGuardSeconds = 0,
        # Process priority class; default from SRWZ_LAUNCH_PRIORITY (quiet mode: BelowNormal
        # for every launch while Wyn works at the PC), else the system default.
        [ValidateSet('', 'Idle', 'BelowNormal', 'Normal', 'AboveNormal', 'High')] [string]$Priority = ''
    )
    $exe = Resolve-QuietExe $FilePath
    $cmd = (@(ConvertTo-QuietArg $exe) + @($ArgumentList | Where-Object { $null -ne $_ } | ForEach-Object { ConvertTo-QuietArg "$_" })) -join ' '
    if ($RawArguments) { $cmd += " " + $RawArguments }   # verbatim (cmd.exe /c lines)
    # A .bat/.cmd runs through cmd.exe (CreateProcess does that itself only for the app name).
    if ($exe -match '\.(bat|cmd)$') { $cmd = "`"$env:ComSpec`" /d /s /c `"$cmd`""; $exe = $env:ComSpec }
    # [NullString]::Value: a plain $null would reach C# as "" (an empty environment, inherit-std).
    $block = [NullString]::Value
    if ($Env -and $Env.Count) {
        $vars = @{}
        foreach ($e in [Environment]::GetEnvironmentVariables().GetEnumerator()) { $vars[[string]$e.Key] = [string]$e.Value }
        foreach ($k in $Env.Keys) { if ($null -eq $Env[$k]) { $vars.Remove([string]$k) } else { $vars[[string]$k] = [string]$Env[$k] } }
        $block = (($vars.Keys | Sort-Object | ForEach-Object { "$_=$($vars[$_])" }) -join "`0") + "`0`0"
    }
    $o = if ($InheritStd) { "" } elseif ($StdOut) { [IO.Path]::GetFullPath($StdOut) } else { [NullString]::Value }
    $e2 = if ($InheritStd -and -not $StdErr) { "" } elseif ($StdErr) { [IO.Path]::GetFullPath($StdErr) } else { [NullString]::Value }
    if ($InheritStd -and $StdOut) { $o = [IO.Path]::GetFullPath($StdOut) }
    $wd = if ($WorkingDirectory) { (Resolve-Path -LiteralPath $WorkingDirectory).Path } else { (Get-Location -PSProvider FileSystem).ProviderPath }
    $x = -1; $y = -1; if ($Position.Count -eq 2) { $x = $Position[0]; $y = $Position[1] }
    # Sharing our console is only for -InheritStd when we have one (a .bat in a terminal):
    # the child writes to that console as `&` would. Everything else: a console without a window.
    $share = [bool]$InheritStd -and [SrwzLaunch.Native]::HasConsole()
    $s = [SrwzLaunch.Native]::Create($exe, $cmd, $wd, $block, $o, $e2, [bool]$Append, $script:QuietShow[$Window], $x, $y, $share)
    try { $p = [Diagnostics.Process]::GetProcessById($s.Pid); $null = $p.Handle }
    catch { [SrwzLaunch.Native]::Abort($s); throw }
    $prio = if ($Priority) { $Priority } else { "$env:SRWZ_LAUNCH_PRIORITY" }
    if ($prio) { try { $p.PriorityClass = [Diagnostics.ProcessPriorityClass]$prio } catch { Write-Warning "launch: priority '$prio' not applied: $_" } }
    if ($FocusGuardSeconds -gt 0) {
        $g = [SrwzLaunch.Native]::GuardFocus($s.Pid, $FocusGuardSeconds * 1000)
        $p | Add-Member -NotePropertyName FocusGuard -NotePropertyValue $g
    }
    [SrwzLaunch.Native]::Resume($s)
    if ($Position.Count -eq 2) { Move-QuietWindow $p $x $y -WaitSeconds 10 | Out-Null }
    $p
}

# Moves a process's main window without activating it or changing its z-order. Returns $true
# when it found the window within WaitSeconds.
function Move-QuietWindow($Process, [int]$X, [int]$Y, [int]$WaitSeconds = 10) {
    $deadline = (Get-Date).AddSeconds($WaitSeconds)
    while ((Get-Date) -lt $deadline -and -not $Process.HasExited) {
        $Process.Refresh()
        if ($Process.MainWindowHandle -ne [IntPtr]::Zero) {
            # SWP_NOSIZE 1 | SWP_NOZORDER 4 | SWP_NOACTIVATE 0x10
            return [SrwzLaunch.Native]::SetWindowPos($Process.MainWindowHandle, [IntPtr]::Zero, $X, $Y, 0, 0, 0x15)
        }
        Start-Sleep -Milliseconds 200
    }
    $false
}

# Waits for the process; returns its exit code, or $null on timeout (-Kill: kill it then, and
# return $null all the same).
function Wait-Quiet($Process, [int]$TimeoutSeconds = 0, [switch]$Kill) {
    $ms = if ($TimeoutSeconds -gt 0) { $TimeoutSeconds * 1000 } else { -1 }
    if ($Process.WaitForExit($ms)) { $Process.WaitForExit(); return $Process.ExitCode }
    if ($Kill) { Stop-QuietTree $Process.Id; $null = $Process.WaitForExit(5000) }
    $null
}

# Kills a process and its children (taskkill /T, itself started quietly).
function Stop-QuietTree([int]$Id) {
    $tk = Join-Path $env:SystemRoot "System32\taskkill.exe"
    $k = Start-Quiet $tk -ArgumentList "/PID", "$Id", "/T", "/F"
    $null = $k.WaitForExit(15000)
}

# Reads the complete lines of a growing log from byte $pos on; returns @{ Lines; Pos }.
function Read-QuietNew([string]$path, [long]$pos, [switch]$all) {
    $fs = [IO.File]::Open($path, 'Open', 'Read', 'ReadWrite')
    try {
        $len = $fs.Length - $pos
        if ($len -le 0) { return @{ Lines = @(); Pos = $pos } }
        $buf = New-Object byte[] $len
        $null = $fs.Seek($pos, 'Begin'); $n = $fs.Read($buf, 0, $len)
    } finally { $fs.Close() }
    $end = if ($all) { $n } else { [Array]::LastIndexOf($buf, [byte]10, $n - 1) + 1 }
    if ($end -le 0) { return @{ Lines = @(); Pos = $pos } }
    $text = [Console]::OutputEncoding.GetString($buf, 0, $end)
    $lines = @($text -split "\r?\n")
    if ($lines.Count -and $lines[-1] -eq '') { $lines = @($lines | Select-Object -SkipLast 1) }
    @{ Lines = $lines; Pos = $pos + $end }
}

# Runs a command quietly to the end, like `& exe args`: stdout lines to the pipeline, stderr
# lines to the host (-MergeErr: into stdout, like 2>&1), $global:LASTEXITCODE = its exit code.
# -Echo prints stdout lines on the host as they come (long builds and runs); -Stream sends them
# down the pipeline as they come (a script whose output is the child's). -TimeoutSeconds:
# the process tree is killed then and LASTEXITCODE is -1. -StdOut/-StdErr keep the output in
# those files instead of temporary ones.
function Invoke-Quiet {
    param(
        [Parameter(Mandatory = $true, Position = 0)] [string]$FilePath,
        [Parameter(Position = 1)] [string[]]$ArgumentList = @(),
        [string]$RawArguments = "",
        [string]$WorkingDirectory = "", [hashtable]$Env = $null,
        [switch]$MergeErr, [switch]$Echo, [switch]$Stream, [int]$TimeoutSeconds = 0,
        [string]$StdOut = "", [string]$StdErr = ""
    )
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("quiet-" + [guid]::NewGuid().ToString('N').Substring(0, 12))
    $out = if ($StdOut) { $StdOut } else { "$tmp.out.txt" }
    $err = if ($MergeErr) { $out } elseif ($StdErr) { $StdErr } else { "$tmp.err.txt" }
    $p = Start-Quiet $FilePath -ArgumentList $ArgumentList -RawArguments $RawArguments -WorkingDirectory $WorkingDirectory -Env $Env -StdOut $out -StdErr $err -Window Hidden
    $pos = [long]0; $all = New-Object System.Collections.Generic.List[string]
    $deadline = if ($TimeoutSeconds -gt 0) { (Get-Date).AddSeconds($TimeoutSeconds) } else { [DateTime]::MaxValue }
    $timedOut = $false
    while (-not $p.WaitForExit(200)) {
        if ($Echo -or $Stream) {
            $r = Read-QuietNew $out $pos; $pos = $r.Pos
            foreach ($l in $r.Lines) { if ($Stream) { $l } else { Write-Host $l; $all.Add($l) } }
        }
        if ((Get-Date) -gt $deadline) { $timedOut = $true; Stop-QuietTree $p.Id; $null = $p.WaitForExit(5000); break }
    }
    $p.WaitForExit()
    $r = Read-QuietNew $out $pos -all
    foreach ($l in $r.Lines) { if ($Stream) { $l } else { if ($Echo) { Write-Host $l }; $all.Add($l) } }
    if (-not $MergeErr -and (Test-Path -LiteralPath $err)) {
        foreach ($l in (Read-QuietNew $err 0 -all).Lines) { Write-Host $l }
    }
    $global:LASTEXITCODE = if ($timedOut) { -1 } else { $p.ExitCode }
    if (-not $StdOut) { Remove-Item -LiteralPath "$tmp.out.txt" -Force -ErrorAction SilentlyContinue }
    if (-not $StdErr) { Remove-Item -LiteralPath "$tmp.err.txt" -Force -ErrorAction SilentlyContinue }
    $all.ToArray()
}

function Get-VisibleWindows {
    foreach ($w in [SrwzLaunch.Native]::Windows()) {
        $name = (Get-Process -Id $w.Pid -ErrorAction SilentlyContinue).ProcessName
        [pscustomobject]@{ Hwnd = [int64]$w.Hwnd; Pid = $w.Pid; Process = $name; Title = $w.Title; Class = $w.Class }
    }
}
function Get-ForegroundHwnd { [int64][SrwzLaunch.Native]::GetForegroundWindow() }

# Command line: options, then -- (optional) and the command.
$launchCli = @{ SelfTest = $false; NoWait = $false; TimeoutSeconds = 0; Cwd = ""; StdOut = ""; StdErr = ""; Window = "NoActivate"; Command = @() }
if ($MyInvocation.InvocationName -ne '.') {
    for ($i = 0; $i -lt $args.Count; $i++) {
        $a = [string]$args[$i]
        if ($a -eq '--') { $launchCli.Command = @($args | Select-Object -Skip ($i + 1)); break }
        elseif ($a -eq '-SelfTest') { $launchCli.SelfTest = $true }
        elseif ($a -eq '-NoWait') { $launchCli.NoWait = $true }
        elseif ($a -match '^-(TimeoutSeconds|Cwd|StdOut|StdErr|Window)$') { $launchCli[$Matches[1]] = [string]$args[++$i] }
        else { $launchCli.Command = @($args | Select-Object -Skip $i); break }
    }
}

if ($MyInvocation.InvocationName -ne '.' -and $launchCli.SelfTest) {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("launch-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    New-Item -ItemType Directory -Force $tmp | Out-Null
    $ps = Join-Path $PSHOME "powershell.exe"
    # Windows are judged by owner: only those of the processes this test starts (and their
    # children, and terminal windows titled with their exe names) count, so a window the user
    # opens meanwhile cannot fail it.
    $watch = [SrwzLaunch.Native]::WatchTree([int[]]@($PID)); $fg = Get-ForegroundHwnd
    Check ((ConvertTo-QuietArg 'a b') -eq '"a b"' -and (ConvertTo-QuietArg 'x') -eq 'x' -and (ConvertTo-QuietArg '"q"') -eq '"q"' -and
           (ConvertTo-QuietArg 'c:\a b\') -eq '"c:\a b\\"' -and (ConvertTo-QuietArg '') -eq '""') "argument quoting"
    # Exit code, stdout/stderr files, working dir, env (inherited and per child), arguments.
    $env:QUIET_SELFTEST_A = "inherited"
    $script1 = Join-Path $tmp "child.ps1"
    @'
param($a, $b)
[Console]::Out.WriteLine("args=[$a][$b] cwd=$((Get-Location).Path) A=$env:QUIET_SELFTEST_A B=$env:QUIET_SELFTEST_B")
[Console]::Error.WriteLine("to stderr")
exit 7
'@ | Set-Content $script1 -Encoding utf8
    $o = Join-Path $tmp "c.out.txt"; $e = Join-Path $tmp "c.err.txt"
    $p = Start-Quiet $ps -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", $script1, "one two", 'q"x' `
        -WorkingDirectory $tmp -StdOut $o -StdErr $e -Env @{ QUIET_SELFTEST_B = "child" }
    Check ($p -is [Diagnostics.Process] -and $p.Id -gt 0) "returns a Process (pid $($p.Id))"
    $rc = Wait-Quiet $p -TimeoutSeconds 30
    Check ($rc -eq 7) "exit code (got $rc)"
    $text = Get-Content $o -Raw
    Check ($text -match [regex]::Escape("args=[one two][q`"x]")) "arguments with spaces and quotes ($($text.Trim()))"
    Check ($text -match [regex]::Escape("cwd=$tmp")) "working directory"
    Check ($text -match 'A=inherited B=child') "environment: inherited plus per-child"
    Check ($null -eq $env:QUIET_SELFTEST_B) "per-child env does not leak into ours"
    Check ((Get-Content $e -Raw) -match 'to stderr') "stderr to its own file"
    # Invoke-Quiet: captured lines and LASTEXITCODE; merge; timeout kills.
    $lines = @(Invoke-Quiet $ps @("-NoProfile", "-Command", "'l1'; 'l2'; exit 3"))
    Check ($lines.Count -eq 2 -and $lines[1] -eq 'l2' -and $LASTEXITCODE -eq 3) "Invoke-Quiet captures stdout and the exit code ($($lines -join ',') rc $LASTEXITCODE)"
    $lines = @(Invoke-Quiet $ps @("-NoProfile", "-Command", "[Console]::Error.WriteLine('e1'); 'o1'") -MergeErr)
    Check (($lines -join ',') -match 'e1' -and ($lines -join ',') -match 'o1') "Invoke-Quiet -MergeErr"
    $seen = @(Invoke-Quiet $ps @("-NoProfile", "-Command", "'s1'; [Console]::Out.Flush(); Start-Sleep -Milliseconds 1500; 's2'") -Stream | ForEach-Object { "$_@$((Get-Date).Ticks)" })
    $gap = if ($seen.Count -eq 2) { ([int64]($seen[1] -split '@')[1] - [int64]($seen[0] -split '@')[1]) / 1e7 } else { 0 }
    Check ($seen.Count -eq 2 -and $gap -gt 0.8) "Invoke-Quiet -Stream passes lines on as they come (gap $([math]::Round($gap, 1)) s)"
    $t0 = Get-Date
    $null = Invoke-Quiet $ps @("-NoProfile", "-Command", "Start-Sleep 60") -TimeoutSeconds 2
    Check ($LASTEXITCODE -eq -1 -and ((Get-Date) - $t0).TotalSeconds -lt 20) "Invoke-Quiet -TimeoutSeconds kills (rc $LASTEXITCODE)"
    # Wait timeout, then kill.
    $p = Start-Quiet $ps -ArgumentList "-NoProfile", "-Command", "Start-Sleep 60"
    Check ($null -eq (Wait-Quiet $p -TimeoutSeconds 1 -Kill) -and $p.HasExited) "Wait-Quiet timeout + -Kill"
    # A child that exits at once still has its exit code.
    $p = Start-Quiet (Join-Path $env:SystemRoot "System32\cmd.exe") -ArgumentList "/c", "exit 5"
    Check ((Wait-Quiet $p 10) -eq 5) "instant exit keeps its exit code"
    $lines = @(Invoke-Quiet (Join-Path $env:SystemRoot "System32\cmd.exe") -RawArguments "/d /s /c `"echo raw `"a b`" && exit /b 2`"" -MergeErr)
    Check ($LASTEXITCODE -eq 2 -and ($lines -join ',') -match 'raw "a b"') "-RawArguments: a cmd.exe line as is (rc $LASTEXITCODE, '$($lines -join ',')')"
    # A .bat through cmd.exe, append mode.
    $bat = Join-Path $tmp "t.bat"; Set-Content $bat "@echo bat %1`r`n@exit /b 4" -Encoding ascii
    $p = Start-Quiet $bat -ArgumentList "arg 1" -StdOut $o -StdErr $o -Append
    Check ((Wait-Quiet $p 10) -eq 4 -and (Get-Content $o -Raw) -match 'bat "arg 1"' -and (Get-Content $o -Raw) -match 'args=') ".bat file, appended log"
    # The command-line form, from a shell: exit code and stdout pass through.
    $cliOut = @(& $ps -NoProfile -ExecutionPolicy Bypass -File $PSCommandPath -TimeoutSeconds 60 -- $ps -NoProfile -Command "'via-cli'; exit 6")
    Check ($LASTEXITCODE -eq 6 -and ($cliOut -join ',') -match 'via-cli') "command line: output and exit code (rc $LASTEXITCODE, '$($cliOut -join ',')')"
    # A GUI child (a WinForms dialog, which activates itself when shown): minimised, not in the
    # foreground.
    $form = "Add-Type -AssemblyName System.Windows.Forms; `$f = New-Object Windows.Forms.Form; `$f.Text = 'launch-selftest'; [void]`$f.ShowDialog()"
    $np = Start-Quiet $ps -ArgumentList "-NoProfile", "-Command", $form -Window Minimized
    for ($i = 0; $i -lt 50 -and $np.MainWindowHandle -eq [IntPtr]::Zero; $i++) { Start-Sleep -Milliseconds 200; $np.Refresh() }
    Start-Sleep -Milliseconds 500
    $fg2 = Get-ForegroundHwnd
    Check ($np.MainWindowHandle -ne [IntPtr]::Zero) "the GUI child has a window"
    Check ($fg2 -ne [int64]$np.MainWindowHandle) "a GUI child does not take the foreground"
    Check ([SrwzLaunch.Native]::IsIconic($np.MainWindowHandle)) "-Window Minimized: it starts minimised"
    Stop-QuietTree $np.Id
    # One that activates itself on purpose (as GLFW does): the focus guard hands it back.
    $grab = "Add-Type -AssemblyName System.Windows.Forms; `$f = New-Object Windows.Forms.Form; `$f.Text = 'launch-selftest-grab'; `$f.Add_Shown({ `$f.Activate() }); [void]`$f.ShowDialog()"
    $fgBefore = Get-ForegroundHwnd
    $gp = Start-Quiet $ps -ArgumentList "-NoProfile", "-Command", $grab -FocusGuardSeconds 15
    $stolen = $false
    for ($i = 0; $i -lt 100 -and -not $gp.FocusGuard.Done; $i++) {
        Start-Sleep -Milliseconds 100; $gp.Refresh()
        if ($gp.MainWindowHandle -ne [IntPtr]::Zero -and (Get-ForegroundHwnd) -eq [int64]$gp.MainWindowHandle) { $stolen = $true }
    }
    $gp.Refresh()
    Check ($gp.MainWindowHandle -ne [IntPtr]::Zero -and (Get-ForegroundHwnd) -ne [int64]$gp.MainWindowHandle) "focus guard: a child that activates itself does not keep the foreground (handed back $($gp.FocusGuard.Restored)x, seen in front at a 100 ms poll: $stolen)"
    Stop-QuietTree $gp.Id
    Start-Sleep -Milliseconds 500
    $watch.Stop = $true; Start-Sleep -Milliseconds 200
    $mine = @($watch.Ours.ToArray()); $gui = @($mine | Where-Object { $_.Title -like 'launch-selftest*' }); $new = @($mine | Where-Object { $_.Title -notlike 'launch-selftest*' })
    Check ($gui.Count -eq 2 -and $new.Count -eq 0) "no windows from our children but the 2 GUI test forms (watched $($watch.Pids.Count) processes; forms $($gui.Count), others $($new.Count)$(if ($new) { ': ' + (($new | ForEach-Object { "pid $($_.Pid) [$($_.Class)] '$($_.Title)'" }) -join ', ') }))"
    # The foreground may change because the user switched windows; it must not be ours.
    $fgNow = Get-ForegroundHwnd
    $ours = [bool]($mine | Where-Object { [int64]$_.Hwnd -eq $fgNow })
    Check (($fgNow -eq $fg) -or -not $ours) "foreground not taken by a child ($(if ($fgNow -eq $fg) { 'unchanged' } elseif ($ours) { 'ours!' } else { 'the user switched windows' }))"
    Remove-Item Env:QUIET_SELFTEST_A -ErrorAction SilentlyContinue
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}

if ($MyInvocation.InvocationName -ne '.' -and $launchCli.Command.Count) {
    $exe = $launchCli.Command[0]; $rest = @($launchCli.Command | Select-Object -Skip 1)
    $sp = @{ ArgumentList = $rest; InheritStd = $true; Window = $launchCli.Window }
    if ($launchCli.Cwd) { $sp.WorkingDirectory = $launchCli.Cwd }
    if ($launchCli.StdOut) { $sp.StdOut = $launchCli.StdOut }
    if ($launchCli.StdErr) { $sp.StdErr = $launchCli.StdErr }
    $p = Start-Quiet $exe @sp
    if ($launchCli.NoWait) { "pid $($p.Id)"; exit 0 }
    $rc = Wait-Quiet $p -TimeoutSeconds ([int]$launchCli.TimeoutSeconds) -Kill
    if ($null -eq $rc) { [Console]::Error.WriteLine("launch: timed out after $($launchCli.TimeoutSeconds) s, killed"); exit 124 }
    exit $rc
}
