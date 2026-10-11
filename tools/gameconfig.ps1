# Shared helpers for the per-game config (config\game.json) and the main checkout's root.
# Dot-source it:   . "$PSScriptRoot\gameconfig.ps1"
#     Get-MainRoot                  the MAIN checkout (git's common dir), also from a worktree
#                                   under local\wt-*; falls back to this script's checkout
#     Get-GameConfig [-Path p]      config\game.json of this checkout (or $env:PS2X_GAME_CONFIG)
#     Resolve-GamePath $cfg 'runner'  a paths.<key> entry as an absolute path under the main root
#     Get-RoutePadScript <route> [cfgPath]  PS2X_PAD_SCRIPT for a route name, route file, @file or
#                                   inline steps (tools\route.py); throws on a bad route
#     Expand-PadScriptValue <value> [cfgPath]  '@file' -> its expansion; anything else unchanged
# Everything game-specific (ELF, disc, runner, recompiler config, references, checkpoints)
# lives in the config, so the tools serve other PS2 ports unchanged.

function Get-MainRoot {
    $here = Split-Path -Parent $PSScriptRoot
    $common = $null
    try { $common = & git -C $PSScriptRoot rev-parse --path-format=absolute --git-common-dir 2>$null } catch { }
    if ($common -and $LASTEXITCODE -eq 0 -and $common) {
        $r = Split-Path -Parent ($common.Trim() -replace '/', '\')
        if (Test-Path $r) { return $r }
    }
    $here
}

function Get-GameConfig([string]$Path = "") {
    if (-not $Path) { $Path = $env:PS2X_GAME_CONFIG }
    if (-not $Path) { $Path = Join-Path (Split-Path -Parent $PSScriptRoot) "config\game.json" }
    if (-not (Test-Path $Path)) { throw "game config not found: $Path" }
    $cfg = Get-Content -Raw $Path | ConvertFrom-Json
    foreach ($k in 'name', 'paths', 'checkpoints') {
        if (-not $cfg.PSObject.Properties[$k]) { throw "game config ${Path}: missing '$k'" }
    }
    foreach ($k in 'runner', 'elf', 'runs') {
        if (-not $cfg.paths.PSObject.Properties[$k]) { throw "game config ${Path}: missing paths.$k" }
    }
    $cfg | Add-Member -NotePropertyName _path -NotePropertyValue (Resolve-Path $Path).Path -Force
    $cfg | Add-Member -NotePropertyName _root -NotePropertyValue (Get-MainRoot) -Force
    $cfg
}

# Absolute path for paths.<key> (relative entries are under the main root). $null when unset.
function Resolve-GamePath($cfg, [string]$key) {
    $p = $cfg.paths.PSObject.Properties[$key]
    if (-not $p -or -not $p.Value) { return $null }
    $v = $p.Value -replace '/', '\'
    if ([IO.Path]::IsPathRooted($v)) { $v } else { Join-Path $cfg._root $v }
}

# PS2X_PAD_SCRIPT for a route (config\game.json pad.routes/macros, config\routes\<name>.txt, a
# route file, @file or inline steps), expanded by tools\route.py. Warnings (unverified route) go
# to the host; a bad route throws.
function Get-RoutePadScript([string]$Route, [string]$ConfigPath = "") {
    if (-not (Get-Command Invoke-Quiet -ErrorAction SilentlyContinue)) { . (Join-Path $PSScriptRoot "launch.ps1") }
    $a = @((Join-Path $PSScriptRoot "route.py"), $Route)
    if ($ConfigPath) { $a += @("--game", $ConfigPath) }
    $lines = @(Invoke-Quiet python $a)
    if ($LASTEXITCODE -ne 0 -or -not $lines.Count -or -not $lines[-1]) { throw "route '$Route' did not expand (tools\route.py exit $LASTEXITCODE)" }
    "$($lines[-1])".Trim()
}

# A PS2X_PAD_SCRIPT value: '@<file>' is a route file expanded here (the runtime takes only the
# expanded string); anything else is returned unchanged.
function Expand-PadScriptValue([string]$Value, [string]$ConfigPath = "") {
    if ($Value -and $Value.StartsWith('@')) { Get-RoutePadScript $Value $ConfigPath } else { $Value }
}
