# Build a PS2Recomp worktree (local\wt-<name>, one branch under review) into its own
# out\build, with the same options as tools\configure-ps2recomp.bat and the main build's
# already-fetched dependencies (third_party\PS2Recomp\out\build\_deps, offline), so a
# worktree never downloads anything and never touches the main build.
#     powershell -File tools\wt-build.ps1 -Worktree local\wt-snap -Targets ps2x_tests
#     powershell -File tools\wt-build.ps1 -Worktree local\wt-snap -Targets ps2EntryRunner [-Config Release]
#     powershell -File tools\wt-build.ps1 -Worktree local\wt-snap -Configure            (configure only)
#     powershell -File tools\wt-build.ps1 ... -DryRun          print the commands, run nothing
#     powershell -File tools\wt-build.ps1 -Worktree local\wt-x -GameDir local\recomp-x\output
#         [-OverlayDir local\recomp-x\overlay_build] -Targets ps2EntryRunner
#                                   build against a private copy of the generated game code
#                                   (default: the main checkout's local\recomp\output and
#                                   overlay_build); a dir other than the cache's reconfigures
#     powershell -File tools\wt-build.ps1 -SelfTest
# Configures when out\build has no CMakeCache.txt or -Configure is given (a configure
# regenerates the game's project files: ~2 min). Any target that includes the game
# (ps2EntryRunner) holds the "build" lock (tools\lock.ps1, fair queue) for the whole build:
# the full game build needs most of the RAM. -NoLock skips that (unit-test-only builds
# need no lock). Prints BUILD_RC=<code> last; exit code = that code.
param(
    [string]$Worktree = "",
    [string[]]$Targets = @(),
    [string]$Config = "Release",
    [string]$DepsDir = "",
    [string]$GameDir = "",
    [string]$OverlayDir = "",
    [int]$Parallel = 0,
    [switch]$BelowNormal,
    [switch]$Configure,
    [switch]$NoLock,
    [switch]$DryRun,
    [switch]$SelfTest
)
$ErrorActionPreference = "Stop"
# lock.ps1 has a param block of its own (-SelfTest among them): keep ours before dot-sourcing.
# "-Targets a,b" through powershell -File arrives as one string "a,b": split it.
$Targets = @($Targets | ForEach-Object { "$_" -split ',' } | ForEach-Object { $_.Trim() } | Where-Object { $_ })
$opt = @{ Worktree = $Worktree; Targets = $Targets; Config = $Config; DepsDir = $DepsDir; GameDir = $GameDir; OverlayDir = $OverlayDir
          Configure = [bool]$Configure; NoLock = [bool]$NoLock; DryRun = [bool]$DryRun; SelfTest = [bool]$SelfTest }
# -Parallel N caps the build's jobs (cmake --parallel N; default: all cores); -BelowNormal runs the
# compiler tree at below-normal priority (quiet mode while Wyn works at the PC).
$script:parallel = $Parallel
$script:belowNormal = [bool]$BelowNormal
. (Join-Path $PSScriptRoot "gameconfig.ps1")
. (Join-Path $PSScriptRoot "lock.ps1")
. (Join-Path $PSScriptRoot "launch.ps1")  # Invoke-Quiet: cmake/MSBuild/cl without a console window
$root = Get-MainRoot

# Messages go to the host and to $script:trace (the self-test reads them); function return
# values stay exit codes only.
$script:trace = @()
function Say([string]$s) { $script:trace += $s; Write-Host $s }

$script:cmake = "C:\Program Files\CMake\bin\cmake.exe"
$script:vcvars = "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools\VC\Auxiliary\Build\vcvars64.bat"
$script:lockTargets = @("ps2EntryRunner")

# The configure command line: main's options plus one FETCHCONTENT_SOURCE_DIR per fetched
# dependency found in $deps (elfio-src -> ELFIO, nlohmann_json-src -> NLOHMANN_JSON).
function Get-ConfigureArgs([string]$src, [string]$out, [string]$deps, [string]$mainRoot, [string]$gameDir = "", [string]$overlayDir = "") {
    if (-not $gameDir) { $gameDir = "$mainRoot\local\recomp\output" }
    if (-not $overlayDir) { $overlayDir = "$mainRoot\local\recomp\overlay_build" }
    $args = @("-S", $src, "-B", $out, "-G", "Visual Studio 17 2022", "-A", "x64",
              "-DPS2X_BUILD_STUDIO=OFF", "-DCMAKE_POLICY_VERSION_MINIMUM=3.5",
              "-DPS2X_GAME_DIR=$gameDir",
              "-DPS2X_OVERLAY_DIR=$overlayDir",
              "-DFETCHCONTENT_FULLY_DISCONNECTED=ON",
              "-DPS2X_DOWNLOAD_CACHE=$mainRoot\local\dlcache")
    foreach ($d in @(Get-ChildItem -Directory $deps -Filter '*-src' -ErrorAction SilentlyContinue | Sort-Object Name)) {
        $name = ($d.Name -replace '-src$', '').ToUpperInvariant() -replace '-', '_'
        $args += "-DFETCHCONTENT_SOURCE_DIR_$name=$($d.FullName)"
    }
    $args
}

function Quote([string[]]$parts) { ($parts | ForEach-Object { if ($_ -match '[ ="]') { '"' + $_ + '"' } else { $_ } }) -join ' ' }

# Runs a cmd.exe line, echoing its output, and returns its exit code. Under Windows PowerShell
# 5.1 a native program's stderr (e.g. a CMake deprecation warning) piped through 2>&1 becomes
# an error record, which "Stop" turns into a terminating error: run it under "Continue" and
# judge success by the exit code only.
function Invoke-CmdLine([string]$line) {
    $prev = $ErrorActionPreference
    $ErrorActionPreference = "Continue"
    try {
        # Quiet child (tools\launch.ps1): its stderr goes to the log file, never an error record.
        $null = Invoke-Quiet (Join-Path $env:SystemRoot "System32\cmd.exe") -RawArguments "/d /s /c `"$line`"" -MergeErr -Echo
        $rc = $LASTEXITCODE
    } finally { $ErrorActionPreference = $prev }
    $rc
}

# Runs cmake with MSVC on PATH (vcvars64 in the same cmd), returns the exit code.
function Invoke-CMake([string[]]$cmakeArgs, [switch]$dry) {
    if ($script:parallel -gt 0 -and $cmakeArgs -contains "--parallel") {
        $i = [array]::IndexOf($cmakeArgs, "--parallel")
        $cmakeArgs = @($cmakeArgs[0..$i]) + @("$script:parallel") + @($cmakeArgs[($i + 1)..($cmakeArgs.Count - 1)])
    }
    # start /wait /b /belownormal: same console, below-normal priority inherited by the compilers,
    # exit code passed on.
    $prefix = if ($script:belowNormal) { 'start "" /b /wait /belownormal ' } else { '' }
    $line = "call `"$script:vcvars`" >nul 2>&1 && $prefix`"$script:cmake`" " + (Quote $cmakeArgs)
    if ($dry) { Say "DRYRUN: cmd /c $line"; return 0 }
    Invoke-CmdLine $line
}

# Absolute path of a -GameDir/-OverlayDir (relative = to the main root); "" stays "".
function Resolve-GenDir([string]$dir) {
    if (-not $dir) { return "" }
    if (-not [IO.Path]::IsPathRooted($dir)) { $dir = Join-Path $root $dir }
    [IO.Path]::GetFullPath($dir).TrimEnd('\')
}

# The value of an entry in a CMakeCache.txt ("" when absent), with backslashes.
function Get-CacheValue([string]$cache, [string]$key) {
    if (-not (Test-Path $cache)) { return "" }
    $m = Select-String -Path $cache -Pattern ("^" + [regex]::Escape($key) + ":[A-Z]+=(.*)$") | Select-Object -First 1
    if ($m) { return ($m.Matches[0].Groups[1].Value -replace '/', '\').TrimEnd('\') }
    ""
}

function Build-Worktree([string]$wt, [string[]]$targets, [string]$config, [string]$deps, [switch]$configure, [switch]$noLock, [switch]$dry,
                        [string]$gameDir = "", [string]$overlayDir = "") {
    $src = (Resolve-Path $wt -ErrorAction SilentlyContinue)
    if (-not $src) { throw "worktree not found: $wt" }
    $src = $src.Path
    $out = Join-Path $src "out\build"
    $cache = Join-Path $out "CMakeCache.txt"
    $needsConfigure = $configure -or -not (Test-Path $cache)
    # A private game/overlay dir other than the one the cache was configured with.
    foreach ($pair in @(@("PS2X_GAME_DIR", $gameDir), @("PS2X_OVERLAY_DIR", $overlayDir))) {
        if ($pair[1] -and (Test-Path $cache) -and ((Get-CacheValue $cache $pair[0]) -ne $pair[1])) {
            Say "reconfigure: $($pair[0]) changes to $($pair[1])"
            $needsConfigure = $true
        }
    }
    $rc = 0
    if ($needsConfigure) {
        Say "configure: $src -> $out (deps from $deps)"
        $rc = Invoke-CMake (Get-ConfigureArgs $src $out $deps $root $gameDir $overlayDir) -dry:$dry
        if ($rc -ne 0) { return $rc }
    }
    if (-not $targets -or $targets.Count -eq 0) { return $rc }
    $wantsLock = (-not $noLock) -and (@($targets | Where-Object { $script:lockTargets -contains $_ }).Count -gt 0)
    $owner = "wt-build:" + (Split-Path -Leaf $src)
    if ($wantsLock) {
        if ($dry) { Say "DRYRUN: lock build as $owner" }
        elseif (-not (Enter-NamedLock build -Owner $owner -WaitSeconds 14400)) { throw "could not take the build lock" }
    }
    try {
        Say "build: $($targets -join ', ') ($config)"
        $rc = Invoke-CMake (@("--build", $out, "--config", $config, "--parallel", "--target") + $targets) -dry:$dry
    } finally {
        if ($wantsLock -and -not $dry) { $null = Exit-NamedLock build }  # returns a bool: keep it out of the pipeline
    }
    if ($rc -eq 0 -and -not $dry) {
        foreach ($t in $targets) {
            $exe = Get-ChildItem (Join-Path $out "*\$config\$t.exe") -ErrorAction SilentlyContinue | Select-Object -First 1
            if ($exe) { Say "built: $($exe.FullName)" }
        }
    }
    $rc
}

if ($opt.SelfTest) {
    $ok = $true
    function Check($cond, $what) { if ($cond) { "ok:   $what" } else { "FAIL: $what"; $script:ok = $false } }
    $tmp = Join-Path ([IO.Path]::GetTempPath()) ("wt-build-selftest-" + [guid]::NewGuid().ToString('N').Substring(0, 8))
    $fakeWt = Join-Path $tmp "wt-fake"; $fakeDeps = Join-Path $tmp "_deps"
    New-Item -ItemType Directory -Force $fakeWt, (Join-Path $fakeDeps "foo-src"), (Join-Path $fakeDeps "nlohmann_json-src") | Out-Null
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2x_tests") "Release" $fakeDeps -dry
    $text = $script:trace -join "`n"
    Check ($text -match 'DRYRUN: cmd /c call') "dry run prints the commands"
    Check ($text -match 'FETCHCONTENT_SOURCE_DIR_FOO=') "a fetched dependency becomes FETCHCONTENT_SOURCE_DIR_FOO"
    Check ($text -match 'FETCHCONTENT_SOURCE_DIR_NLOHMANN_JSON=') "underscores in dependency names are kept"
    Check ($text -match 'FETCHCONTENT_FULLY_DISCONNECTED=ON') "offline fetch"
    Check ($text -match [regex]::Escape("-DPS2X_GAME_DIR=$root\local\recomp\output")) "game dir from the main root"
    Check ($text -match '--target ps2x_tests') "builds the target"
    Check ($text -notmatch 'lock build') "a unit-test build takes no lock"
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2EntryRunner") "Release" $fakeDeps -dry
    Check (($script:trace -join "`n") -match 'DRYRUN: lock build as wt-build:wt-fake') "the runner build takes the build lock"
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2EntryRunner") "Release" $fakeDeps -dry -noLock
    Check (($script:trace -join "`n") -notmatch 'lock build') "-NoLock skips it"
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2x_tests") "Release" $fakeDeps -dry -gameDir "D:\gen\output" -overlayDir "D:\gen\ovl"
    $text = $script:trace -join "`n"
    Check ($text -match [regex]::Escape("-DPS2X_GAME_DIR=D:\gen\output")) "-GameDir replaces the game dir"
    Check ($text -match [regex]::Escape("-DPS2X_OVERLAY_DIR=D:\gen\ovl")) "-OverlayDir replaces the overlay dir"
    Check ((Resolve-GenDir "local\x\output") -eq "$root\local\x\output") "a relative dir is under the main root"
    New-Item -ItemType File -Force (Join-Path $fakeWt "out\build\CMakeCache.txt") | Out-Null
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2x_tests") "Release" $fakeDeps -dry
    Check (($script:trace -join "`n") -notmatch 'configure:') "an existing cache skips the configure"
    Set-Content (Join-Path $fakeWt "out\build\CMakeCache.txt") "PS2X_GAME_DIR:PATH=D:/gen/output`nPS2X_OVERLAY_DIR:PATH=D:/gen/ovl"
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2x_tests") "Release" $fakeDeps -dry -gameDir "D:\gen\output" -overlayDir "D:\gen\ovl"
    Check (($script:trace -join "`n") -notmatch 'configure:') "the cache's own private dirs: no configure"
    $script:trace = @(); $null = Build-Worktree $fakeWt @("ps2x_tests") "Release" $fakeDeps -dry -gameDir "D:\other\output"
    Check (($script:trace -join "`n") -match 'reconfigure: PS2X_GAME_DIR') "a different game dir reconfigures"
    $rcWarn = Invoke-CmdLine "echo CMake Deprecation Warning 1>&2 & exit /b 0" 6>$null
    Check ($rcWarn -eq 0) "stderr from a tool that succeeds is not an error"
    $rcFail = Invoke-CmdLine "echo failing 1>&2 & exit /b 3" 6>$null
    Check ($rcFail -eq 3) "a tool's exit code is passed on"
    Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}

if (-not $opt.Worktree) { throw "-Worktree <path> is required (e.g. local\wt-snap)" }
$deps = if ($opt.DepsDir) { $opt.DepsDir } else { Join-Path $root "third_party\PS2Recomp\out\build\_deps" }
$wtPath = if ([IO.Path]::IsPathRooted($opt.Worktree)) { $opt.Worktree } else { Join-Path $root $opt.Worktree }
$code = Build-Worktree $wtPath $opt.Targets $opt.Config $deps -configure:$opt.Configure -noLock:$opt.NoLock -dry:$opt.DryRun `
    -gameDir (Resolve-GenDir $opt.GameDir) -overlayDir (Resolve-GenDir $opt.OverlayDir)
"BUILD_RC=$code"
exit $code
