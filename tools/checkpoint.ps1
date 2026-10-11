# Checkpoint library: named game points with our runner snapshot, the paired PCSX2 state, a
# reference picture and a manifest, so a probe or a test starts at the scene instead of replaying
# to it. A checkpoint is a folder <states>\<name>\ (paths.states in config\game.json, default
# local\reference\states):
#     snapshot.bin    our runner snapshot (format 8 loads into any later build whose sections match;
#                     format 7 loads by struct sizes, see PS2Recomp docs/SNAPSHOT-DESIGN.md)
#     pcsx2.p2s       the PCSX2 save state of the same point (optional)
#     reference.png   what the point looks like (a PCSX2 capture, or our own frame; optional)
#     ours.png        our frame at the checkpoint tick (written by save -At, scene)
#     mc0\            the memory card as it was (optional; runs use a scratch copy of it)
#     manifest.json   point, how it was reached, tick, EE cycle, runner build, PCSX2 version, files
#
#     powershell -File tools\checkpoint.ps1 list
#     powershell -File tools\checkpoint.ps1 info <name>
#     powershell -File tools\checkpoint.ps1 save <name> -Snapshot <bin> [-P2s f] [-Reference png]
#         [-Point "text"] [-How "text"] [-Tick N] [-McDir dir] [-Force]      adopt existing files
#     powershell -File tools\checkpoint.ps1 save <name> -At <tick> [-From <checkpoint>] [-Route r]
#         [-Point ..] [-How ..] [-FastForward 4] [-Force]   run the runner (headless) to the tick, from
#         boot or from another checkpoint (chain short hops instead of one long replay), and save
#     powershell -File tools\checkpoint.ps1 load <name> [-Seconds 60] [-Headless] [-Route r]
#     powershell -File tools\checkpoint.ps1 verify <name> [-Frames 300]
#         two deterministic headless runs from the snapshot (PS2X_DETERMINISTIC=1), per-VBlank hashes
#         of RDRAM, GS memory and display registers compared; PASS, or the first differing tick
#         -ExeB <runner>: run b uses another build; -EnvA/-EnvB "K=V,...": extra variables per run
#         (equivalence checks: e.g. -EnvB PS2X_VU1_AOT=0 compares the VU1 recompiler with the interpreter)
#     powershell -File tools\checkpoint.ps1 scene <name> [-Frames 60] [-Tolerance 15] [-Reference f]
#         one deterministic headless run, the frame at tick+Frames against reference.png (or -Reference),
#         PASS/FAIL with a side-by-side diff image (ours | reference | difference)
#     powershell -File tools\checkpoint.ps1 pcsx2 <name> -Times 1482,1490 [-Keys "1483:CIRCLE"] [-Seconds N]
#         PCSX2 from the checkpoint's pcsx2.p2s (tools\pcsx2-capture.ps1 -State, keys posted to the
#         window: no focus change), shots in local\reference\ckpt-<name>\; times are game seconds
#     powershell -File tools\checkpoint.ps1 -SelfTest
# -Exe <runner> picks another runner build (default paths.runner); output goes to
# <runs>\ckpt-<name>-<verb>-<time>\. Exit code 0 = PASS / done, 1 = FAIL, 2 = cannot run.
# Headless, no audio device, no window, no focus steal (tools\launch.ps1, tools\runstop.ps1).
param(
    [Parameter(Position = 0)][string]$Verb = "",
    [Parameter(Position = 1)][string]$Name = "",
    [string]$Snapshot = "",
    [string]$P2s = "",
    [string]$Reference = "",
    [string]$Point = "",
    [string]$How = "",
    [long]$Tick = 0,
    [long]$At = 0,
    [string]$From = "",
    [string]$Route = "",
    [string]$McDir = "",
    [string]$Times = "",
    [string]$Keys = "",
    [string]$FastForward = "4",
    [int]$Frames = 0,
    [double]$Tolerance = 15,
    [int]$Seconds = 0,
    [string]$Exe = "",
    [string]$ExeB = "",
    [string]$EnvA = "",
    [string]$EnvB = "",
    [string]$Config = "",
    [switch]$Headless,
    [switch]$Force,
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
$opt = @{ Verb = $Verb; Name = $Name; Snapshot = $Snapshot; P2s = $P2s; Reference = $Reference; Point = $Point; How = $How
          Tick = $Tick; At = $At; From = $From; Route = $Route; McDir = $McDir; FastForward = $FastForward; Frames = $Frames
          Tolerance = $Tolerance; Seconds = $Seconds; Times = $Times; Keys = $Keys; Exe = $Exe; ExeB = $ExeB; EnvA = $EnvA; EnvB = $EnvB; Config = $Config; Headless = [bool]$Headless
          Force = [bool]$Force; SelfTest = [bool]$SelfTest }
. (Join-Path $PSScriptRoot "gameconfig.ps1")
. (Join-Path $PSScriptRoot "runstop.ps1")

function Get-StatesRoot($cfg, [string]$root) {
    $p = $null
    if ($cfg.paths.PSObject.Properties.Name -contains 'states') { $p = $cfg.paths.states }
    if (-not $p) { $p = "local/reference/states" }
    if ([IO.Path]::IsPathRooted($p)) { return $p }
    return (Join-Path $root $p)
}

function Read-Manifest([string]$dir) {
    $m = Join-Path $dir "manifest.json"
    if (Test-Path $m) { return (Get-Content $m -Raw | ConvertFrom-Json) }
    return $null
}

function Write-Manifest([string]$dir, $manifest) {
    $json = $manifest | ConvertTo-Json -Depth 6
    [IO.File]::WriteAllText((Join-Path $dir "manifest.json"), $json + "`n", (New-Object Text.UTF8Encoding $false))
}

function Get-SnapInfo([string]$bin) {
    $lines = Invoke-Quiet python @((Join-Path $PSScriptRoot "snapinfo.py"), $bin, "--json")
    if ($LASTEXITCODE -ne 0) { throw "snapinfo failed on $bin" }
    return (($lines -join "`n") | ConvertFrom-Json)
}

function Get-RunnerIdentity([string]$exe) {
    $hash = (Get-FileHash $exe -Algorithm SHA256).Hash.Substring(0, 12).ToLower()
    # The PS2Recomp checkout that built it: <checkout>\out\build\ps2xRuntime\<cfg>\ps2EntryRunner.exe
    $checkout = Split-Path (Split-Path (Split-Path (Split-Path (Split-Path $exe))))
    $rev = $null
    if ($checkout -and (Test-Path (Join-Path $checkout ".git"))) {
        $out = Invoke-Quiet git @("-C", $checkout, "rev-parse", "--short", "HEAD")
        if ($LASTEXITCODE -eq 0) { $rev = "$out".Trim() }
        $dirty = Invoke-Quiet git @("-C", $checkout, "status", "--porcelain", "--untracked-files=no")
        if ($rev -and $dirty) { $rev += "+dirty" }
    }
    return [ordered]@{ exe = $exe; sha256 = $hash; ps2recomp = $rev; written = (Get-Item $exe).LastWriteTime.ToString("s") }
}

function Get-Pcsx2Version([string]$root) {
    $exe = Get-ChildItem (Join-Path $root "PCSX2") -Filter "pcsx2*.exe" -ErrorAction SilentlyContinue | Select-Object -First 1
    if (-not $exe) { return $null }
    $v = $exe.VersionInfo.ProductVersion
    if (-not $v) { $v = $exe.VersionInfo.FileVersion }
    return "$($exe.Name) $v".Trim()
}

function Find-CheckpointFile([string]$dir, [string]$preferred, [string]$pattern) {
    $p = Join-Path $dir $preferred
    if (Test-Path $p) { return $p }
    $hit = Get-ChildItem $dir -Filter $pattern -ErrorAction SilentlyContinue | Sort-Object Name | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return $null
}

# The checkpoint's files, also for folders made by hand before this tool (ours_<tick>.bin,
# pcsx2_<s>s.png, MANIFEST.md): the manifest names them, else the usual names, else a pattern.
function Get-Checkpoint([string]$statesRoot, [string]$name) {
    $dir = Join-Path $statesRoot $name
    if (-not (Test-Path $dir)) { throw "no checkpoint '$name' in $statesRoot" }
    $m = Read-Manifest $dir
    $snap = $null; $ref = $null; $p2s = $null; $tick = 0
    if ($m) {
        if ($m.snapshot -and $m.snapshot.file) { $snap = Join-Path $dir $m.snapshot.file; $tick = [long]$m.snapshot.tick }
        if ($m.reference -and $m.reference.file) { $ref = Join-Path $dir $m.reference.file }
        if ($m.pcsx2 -and $m.pcsx2.state) { $p2s = Join-Path $dir $m.pcsx2.state }
    }
    if (-not $snap) { $snap = Find-CheckpointFile $dir "snapshot.bin" "*.bin" }
    if (-not $ref) { $ref = Find-CheckpointFile $dir "reference.png" "pcsx2*.png" }
    if (-not $p2s) { $p2s = Find-CheckpointFile $dir "pcsx2.p2s" "*.p2s" }
    if (-not $tick -and $snap -and ((Split-Path $snap -Leaf) -match '_(\d+)\.bin$')) { $tick = [long]$Matches[1] }
    $mc = Join-Path $dir "mc0"
    return [pscustomobject]@{ Name = $name; Dir = $dir; Manifest = $m; Snapshot = $snap; Reference = $ref; P2s = $p2s
                              Tick = $tick; McDir = $(if (Test-Path $mc) { $mc } else { $null }) }
}

function New-RunDir([string]$runs, [string]$name, [string]$verb) {
    $d = Join-Path $runs ("ckpt-{0}-{1}-{2}" -f $name, $verb, (Get-Date -Format "MMdd-HHmmss"))
    New-Item -ItemType Directory -Force $d | Out-Null
    return $d
}

# One runner run with $envs set (and the usual quiet settings), stopped by the stop file (the
# runner's own PS2X_FRAME_HASH_UNTIL / PS2X_SNAPSHOT_EXIT, or ours at the timeout).
# Returns @{ Exit; How; Out; Err }.
function Invoke-Runner([string]$exe, $paths, [hashtable]$envs, [string]$base, [int]$timeoutSeconds, [bool]$headless = $true) {
    $saved = @{}
    $keys = @($envs.Keys) + @("PS2X_STOP_FILE", "PS2X_HEADLESS", "PS2X_NO_AUDIO", "PS2X_MUTE")
    foreach ($k in $keys) { $saved[$k] = [Environment]::GetEnvironmentVariable($k) }
    try {
        foreach ($k in $envs.Keys) { [Environment]::SetEnvironmentVariable($k, $envs[$k]) }
        $stop = New-RunnerStopFile $base
        if ($headless) { Set-RunnerHeadless } else { Remove-Item Env:PS2X_HEADLESS -ErrorAction SilentlyContinue }
        Set-RunnerAudio $false
        $out = "$base.out.txt"; $err = "$base.err.txt"
        # Below normal unless SRWZ_LAUNCH_PRIORITY says otherwise: Wyn may use the PC meanwhile.
        $prio = if ($env:SRWZ_LAUNCH_PRIORITY) { $env:SRWZ_LAUNCH_PRIORITY } else { 'BelowNormal' }
        $p = Start-Quiet $exe -ArgumentList $paths.elf, $paths.disc -WorkingDirectory $paths.workdir -StdOut $out -StdErr $err `
            -FocusGuardSeconds $(if ($headless) { 0 } else { 60 }) -Priority $prio
        $how = "exited"
        if (-not $p.WaitForExit($timeoutSeconds * 1000)) { $how = "timeout after $timeoutSeconds s: " + (Stop-Runner $p $stop) }
        $p.WaitForExit()
        return @{ Exit = $p.ExitCode; How = $how; Out = $out; Err = $err }
    }
    finally {
        foreach ($k in $saved.Keys) { [Environment]::SetEnvironmentVariable($k, $saved[$k]) }
    }
}

function Get-LoadedTick([string]$outPath) {
    $line = Select-String -Path $outPath -Pattern '\[snapshot\] loaded .* tick=(\d+)' | Select-Object -First 1
    if ($line) { return [long]$line.Matches[0].Groups[1].Value }
    return 0
}

function Get-LoadError([string]$errPath) {
    $line = Select-String -Path $errPath -Pattern 'cannot load|does not match|resumes guest code|snapshot' -SimpleMatch:$false |
        Where-Object { $_.Line -match 'cannot load|does not match|resumes guest code|format' } | Select-Object -First 1
    if ($line) { return $line.Line.Trim() }
    return $null
}

# Scratch copy of the checkpoint's memory card for one run (none: an empty folder), so test runs
# neither see nor change the player's own saves.
function New-ScratchMc([string]$runDir, [string]$tag, $ckpt) {
    $mc = Join-Path $runDir "mc0-$tag"
    New-Item -ItemType Directory -Force $mc | Out-Null
    if ($ckpt -and $ckpt.McDir) { Copy-Item -Recurse -Force (Join-Path $ckpt.McDir "*") $mc }
    return $mc
}

function Invoke-SelfTest {
    $script:ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $py1 = Invoke-Quiet python @((Join-Path $PSScriptRoot "checkpoint.py"), "--selftest") -MergeErr
    Check ($LASTEXITCODE -eq 0) "checkpoint.py selftest ($($py1 -join ' '))"
    $py2 = Invoke-Quiet python @((Join-Path $PSScriptRoot "snapinfo.py"), "--selftest") -MergeErr
    Check ($LASTEXITCODE -eq 0) "snapinfo.py selftest ($($py2 -join ' '))"
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("ckpt-selftest-" + [Guid]::NewGuid().ToString("N").Substring(0, 8))
    New-Item -ItemType Directory $tmp | Out-Null
    try {
        # A hand-made folder (the s01_dialogue_window layout) is found without a manifest.
        $d = Join-Path $tmp "hand_made"
        New-Item -ItemType Directory $d | Out-Null
        [IO.File]::WriteAllBytes((Join-Path $d "ours_56004.bin"), [byte[]](1, 2, 3))
        [IO.File]::WriteAllBytes((Join-Path $d "pcsx2_1480s.png"), [byte[]](1))
        $c = Get-Checkpoint $tmp "hand_made"
        Check ($c.Tick -eq 56004 -and $c.Snapshot -like "*ours_56004.bin" -and $c.Reference -like "*pcsx2_1480s.png") "hand-made folder: snapshot, tick and reference found"
        # A manifest names the files.
        $m = [ordered]@{ name = "m"; snapshot = [ordered]@{ file = "snapshot.bin"; tick = 777 }; reference = [ordered]@{ file = "ref.png" } }
        $d2 = Join-Path $tmp "m"; New-Item -ItemType Directory $d2 | Out-Null
        Write-Manifest $d2 $m
        $c2 = Get-Checkpoint $tmp "m"
        Check ($c2.Tick -eq 777 -and $c2.Snapshot -like "*snapshot.bin" -and $c2.Reference -like "*ref.png") "manifest names the files"
        $threw = $false
        try { Get-Checkpoint $tmp "missing" | Out-Null } catch { $threw = $true }
        Check $threw "a missing checkpoint is an error"
        $mc = New-ScratchMc $tmp "t" $null
        Check ((Test-Path $mc) -and -not (Get-ChildItem $mc)) "scratch memory card without a checkpoint card is empty"
    }
    finally { Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue }
    if ($script:ok) { "checkpoint selftest: PASS"; exit 0 } else { "checkpoint selftest: FAIL"; exit 1 }
}

if ($opt.SelfTest) { Invoke-SelfTest }

$root = Get-MainRoot
$cfg = if ($opt.Config) { Get-GameConfig -Path $opt.Config } else { Get-GameConfig }
$states = Get-StatesRoot $cfg $root
$runs = Resolve-GamePath $cfg 'runs'
$exe = if ($opt.Exe) { (Resolve-Path $opt.Exe).Path } else { Resolve-GamePath $cfg 'runner' }
$paths = @{ elf = (Resolve-GamePath $cfg 'elf'); disc = (Resolve-GamePath $cfg 'disc'); workdir = (Resolve-GamePath $cfg 'workdir') }
$py = Join-Path $PSScriptRoot "checkpoint.py"

switch ($opt.Verb) {
    "list" {
        if (-not (Test-Path $states)) { "no checkpoints ($states missing)"; exit 0 }
        foreach ($d in Get-ChildItem $states -Directory | Sort-Object Name) {
            $c = Get-Checkpoint $states $d.Name
            if (-not $c.Snapshot) { continue }
            $point = if ($c.Manifest -and $c.Manifest.point) { $c.Manifest.point } else { "(no manifest)" }
            $fmt = ""
            try { $i = Get-SnapInfo $c.Snapshot; $fmt = "format $($i.format)" } catch { $fmt = "unreadable" }
            "{0,-28} tick {1,-7} {2,-9} pcsx2:{3} ref:{4}  {5}" -f $c.Name, $c.Tick, $fmt, $(if ($c.P2s) { "y" } else { "-" }), $(if ($c.Reference) { "y" } else { "-" }), $point
        }
        exit 0
    }
    "info" {
        $c = Get-Checkpoint $states $opt.Name
        $c | Format-List Name, Dir, Snapshot, Tick, P2s, Reference, McDir | Out-String
        if ($c.Manifest) { $c.Manifest | ConvertTo-Json -Depth 6 }
        if ($c.Snapshot) { Invoke-Quiet python @((Join-Path $PSScriptRoot "snapinfo.py"), $c.Snapshot) }
        exit 0
    }
    "save" {
        if (-not $opt.Name) { throw "save needs a checkpoint name" }
        $dir = Join-Path $states $opt.Name
        $existing = Test-Path (Join-Path $dir "manifest.json")
        if ($existing -and -not $opt.Force -and -not $opt.Snapshot) { throw "checkpoint '$($opt.Name)' exists (-Force replaces it)" }
        New-Item -ItemType Directory -Force $dir | Out-Null
        # Keep what an earlier manifest said (point, how, ...) unless -Force; the fields below replace theirs.
        $old = if ($opt.Force) { $null } else { Read-Manifest $dir }
        $manifest = [ordered]@{}
        if ($old) { foreach ($p in $old.PSObject.Properties) { $manifest[$p.Name] = $p.Value } }
        $how = $opt.How
        $snapFile = Join-Path $dir "snapshot.bin"
        if ($opt.At -gt 0) {
            $runDir = New-RunDir $runs $opt.Name "save"
            $envs = @{ PS2X_SNAPSHOT_SAVE = (Join-Path $runDir "snap_{tick}.bin"); PS2X_SNAPSHOT_AT = "$($opt.At)"; PS2X_SNAPSHOT_EXIT = "1"
                       PS2X_GS_DUMP = "$($opt.At)"; PS2X_GS_DUMP_DIR = $runDir; PS2X_MC_ROOT = (New-ScratchMc $runDir "save" $null) }
            if ($opt.FastForward -and $opt.FastForward -ne "1") { $envs.PS2X_FASTFORWARD = $opt.FastForward }
            $source = "boot"
            if ($opt.From) {
                $f = Get-Checkpoint $states $opt.From
                $envs.PS2X_SNAPSHOT_LOAD = $f.Snapshot
                $envs.PS2X_MC_ROOT = New-ScratchMc $runDir "save" $f
                $source = "checkpoint $($opt.From) (tick $($f.Tick))"
            }
            if ($opt.Route) { $envs.PS2X_PAD_SCRIPT = Get-RoutePadScript $opt.Route $opt.Config }
            $limit = if ($opt.Seconds) { $opt.Seconds } else { 3600 }
            "running to tick $($opt.At) from $source$(if ($opt.Route) { ", route $($opt.Route)" }) -> $runDir"
            $r = Invoke-Runner $exe $paths $envs (Join-Path $runDir "run") $limit
            $made = Get-ChildItem $runDir -Filter "snap_*.bin" | Select-Object -First 1
            if (-not $made) { "FAIL: no snapshot written ($($r.How), exit $($r.Exit)); $(Get-LoadError $r.Err)"; exit 2 }
            Move-Item -Force $made.FullName $snapFile
            $dump = Get-ChildItem $runDir -Filter "gsdump_*.rgba" | Select-Object -First 1
            if ($dump) { Invoke-Quiet python @($py, "rgba2png", $dump.FullName, (Join-Path $dir "ours.png")) | Out-Null }
            if (-not $how) { $how = "runner from $source to tick $($opt.At)$(if ($opt.Route) { " with route $($opt.Route)" }), fast-forward $($opt.FastForward)" }
        }
        elseif ($opt.Snapshot) {
            $src = (Resolve-Path $opt.Snapshot).Path
            if ($src -ne $snapFile) { Copy-Item -Force $src $snapFile }
        }
        elseif (-not (Test-Path $snapFile)) { throw "save needs -Snapshot <bin> or -At <tick>" }
        if ($opt.P2s) { Copy-Item -Force (Resolve-Path $opt.P2s).Path (Join-Path $dir "pcsx2.p2s") }
        if ($opt.Reference) { Copy-Item -Force (Resolve-Path $opt.Reference).Path (Join-Path $dir "reference.png") }
        if ($opt.McDir) { Copy-Item -Recurse -Force (Resolve-Path $opt.McDir).Path (Join-Path $dir "mc0") }
        $info = Get-SnapInfo $snapFile
        $tick = if ($info.PSObject.Properties.Name -contains 'tick') { [long]$info.tick } elseif ($opt.Tick) { $opt.Tick } elseif ($opt.At) { $opt.At } else { 0 }
        $manifest.name = $opt.Name
        if ($opt.Point) { $manifest.point = $opt.Point }
        if ($how) { $manifest.how = $how }
        $manifest.snapshot = [ordered]@{ file = "snapshot.bin"; tick = $tick; format = $info.format
                                         eeCycle = $(if ($info.PSObject.Properties.Name -contains 'eeCycle') { $info.eeCycle } else { $null })
                                         game = $info.game; build = $info.build; bytes = $info.bytes }
        $manifest.runner = Get-RunnerIdentity $exe
        $p2sPath = Join-Path $dir "pcsx2.p2s"
        if (Test-Path $p2sPath) { $manifest.pcsx2 = [ordered]@{ state = "pcsx2.p2s"; version = (Get-Pcsx2Version $root) } }
        if (Test-Path (Join-Path $dir "reference.png")) { $manifest.reference = [ordered]@{ file = "reference.png" } }
        if (Test-Path (Join-Path $dir "ours.png")) { $manifest.ours = "ours.png" }
        $manifest.saved = (Get-Date).ToString("s")
        Write-Manifest $dir $manifest
        "saved checkpoint $($opt.Name): tick $tick, snapshot format $($info.format), $dir"
        exit 0
    }
    "load" {
        $c = Get-Checkpoint $states $opt.Name
        $runDir = New-RunDir $runs $opt.Name "load"
        $envs = @{ PS2X_SNAPSHOT_LOAD = $c.Snapshot; PS2X_MC_ROOT = (New-ScratchMc $runDir "load" $c) }
        if ($opt.Route) { $envs.PS2X_PAD_SCRIPT = Get-RoutePadScript $opt.Route $opt.Config }
        $secs = if ($opt.Seconds) { $opt.Seconds } else { 60 }
        $r = Invoke-Runner $exe $paths $envs (Join-Path $runDir "run") $secs ([bool]$opt.Headless)
        $t = Get-LoadedTick $r.Out
        if (-not $t) { "FAIL: did not load: $(Get-LoadError $r.Err)"; exit 2 }
        "loaded $($opt.Name) at tick $t, ran $secs s ($($r.How)); logs $runDir"
        exit 0
    }
    "verify" {
        $c = Get-Checkpoint $states $opt.Name
        $frames = if ($opt.Frames) { $opt.Frames } else { 300 }
        $runDir = New-RunDir $runs $opt.Name "verify"
        # Deterministic runs go as fast as the host renders: ~4-60 VBlanks/s depending on the scene.
        $limit = if ($opt.Seconds) { $opt.Seconds } else { [Math]::Max(300, $frames) }
        $results = @()
        foreach ($tag in "a", "b") {
            $envs = @{ PS2X_SNAPSHOT_LOAD = $c.Snapshot; PS2X_DETERMINISTIC = "1"; PS2X_FRAME_HASH = (Join-Path $runDir "$tag.hash")
                       PS2X_FRAME_HASH_UNTIL = "+$frames"; PS2X_MC_ROOT = (New-ScratchMc $runDir $tag $c); PS2X_PAD_SCRIPT = "" }
            # -EnvA / -EnvB "K=V,K=V": extra variables for one run (e.g. PS2X_VU1_AOT=0 against on).
            foreach ($kv in ("$(if ($tag -eq 'a') { $opt.EnvA } else { $opt.EnvB })" -split ',')) {
                if ($kv -match '^\s*([^=]+)=(.*)$') { $envs[$Matches[1].Trim()] = $Matches[2] }
            }
            $runExe = if ($tag -eq "b" -and $opt.ExeB) { (Resolve-Path $opt.ExeB).Path } else { $exe }
            $r = Invoke-Runner $runExe $paths $envs (Join-Path $runDir $tag) $limit
            if (-not (Get-LoadedTick $r.Out)) { "FAIL: run $tag did not load the snapshot: $(Get-LoadError $r.Err)"; exit 2 }
            $results += $r
        }
        $msg = @(Invoke-Quiet python @($py, "hashdiff", (Join-Path $runDir "a.hash"), (Join-Path $runDir "b.hash")) -MergeErr)
        $st = $LASTEXITCODE
        if (-not ($msg -join '').Trim()) { $msg = @("FAIL: the hash comparison printed nothing (exit $st)"); if ($st -eq 0) { $st = 2 } }
        $short = @()
        foreach ($i in 0, 1) {
            $n = @(Get-Content (Join-Path $runDir "$(@('a', 'b')[$i]).hash") -ErrorAction SilentlyContinue).Count
            if ($n -lt $frames) { $short += "run $(@('a', 'b')[$i]) stopped after $n of $frames VBlanks ($($results[$i].How))" }
        }
        if ($short -and $st -eq 0) { $st = 1; $msg = @("INCOMPLETE: " + ($msg -join ' ')) }
        "verify $($opt.Name): $($msg -join ' ')$(if ($short) { '; ' + ($short -join '; ') }) [$frames VBlanks from tick $(Get-LoadedTick $results[0].Out); $runDir]"
        exit $st
    }
    "scene" {
        $c = Get-Checkpoint $states $opt.Name
        $ref = if ($opt.Reference) { (Resolve-Path $opt.Reference).Path } else { $c.Reference }
        if (-not $ref) { "FAIL: checkpoint $($opt.Name) has no reference picture (-Reference <png>)"; exit 2 }
        $frames = if ($opt.Frames) { $opt.Frames } else { 60 }
        $runDir = New-RunDir $runs $opt.Name "scene"
        $limit = if ($opt.Seconds) { $opt.Seconds } else { [Math]::Max(300, $frames) }
        $envs = @{ PS2X_SNAPSHOT_LOAD = $c.Snapshot; PS2X_DETERMINISTIC = "1"; PS2X_FRAME_HASH = (Join-Path $runDir "scene.hash")
                   PS2X_FRAME_HASH_UNTIL = "+$($frames + 30)"; PS2X_MC_ROOT = (New-ScratchMc $runDir "scene" $c); PS2X_PAD_SCRIPT = ""
                   PS2X_GS_DUMP_DIR = $runDir }
        # The dump tick must be after the load tick: from the manifest, else the hash run is the
        # cheap first pass. Format 8 snapshots carry it.
        $start = $c.Tick
        if (-not $start) { $start = [long](Get-SnapInfo $c.Snapshot).tick }
        $envs.PS2X_GS_DUMP = "$($start + $frames)"
        $r = Invoke-Runner $exe $paths $envs (Join-Path $runDir "scene") $limit
        if (-not (Get-LoadedTick $r.Out)) { "FAIL: did not load the snapshot: $(Get-LoadError $r.Err)"; exit 2 }
        $dump = Get-ChildItem $runDir -Filter "gsdump_*.rgba" | Sort-Object Name | Select-Object -First 1
        if (-not $dump) { "FAIL: no frame dumped at tick $($start + $frames) ($($r.How))"; exit 2 }
        $diff = Join-Path $runDir "scene-diff.png"
        $msg = Invoke-Quiet python @($py, "scene", $dump.FullName, $ref, "--tolerance", "$($opt.Tolerance)", "--out", $diff)
        $st = $LASTEXITCODE
        "scene $($opt.Name): $($msg -join ' ') [frame $($dump.BaseName), $runDir]"
        exit $st
    }
    "pcsx2" {
        # PCSX2 from the checkpoint's paired state: tools\pcsx2-capture.ps1 -State, with the
        # state's game time from the manifest (pcsx2.gameSeconds) for its fallback clock.
        $c = Get-Checkpoint $states $opt.Name
        if (-not $c.P2s) { "FAIL: checkpoint $($opt.Name) has no PCSX2 state (save -P2s <file>)"; exit 2 }
        $argsList = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $PSScriptRoot "pcsx2-capture.ps1"),
                      "-Name", "ckpt-$($opt.Name)", "-State", $c.P2s)
        if ($c.Manifest -and $c.Manifest.pcsx2 -and $c.Manifest.pcsx2.gameSeconds) { $argsList += @("-StateTime", "$($c.Manifest.pcsx2.gameSeconds)") }
        if ($opt.Times) { $argsList += @("-Times", $opt.Times) }
        if ($opt.Keys) { $argsList += @("-Keys", $opt.Keys, "-PostKeys") }
        if ($opt.Seconds) { $argsList += @("-Seconds", "$($opt.Seconds)") }
        Invoke-Quiet powershell $argsList -Echo | Out-Null
        exit $LASTEXITCODE
    }
    default {
        "usage: checkpoint.ps1 list | info <name> | save <name> ... | load <name> | verify <name> | scene <name> | pcsx2 <name> | -SelfTest"
        exit 2
    }
}
