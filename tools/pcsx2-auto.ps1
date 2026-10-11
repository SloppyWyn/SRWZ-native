# The private PCSX2 data folder for our automation: local\pcsx2-auto (PCSX2 flag
# "-datapath <folder>"), so scripted runs never touch Wyn's own settings, memory cards,
# save states or window position (Documents\PCSX2).
#     powershell -File tools\pcsx2-auto.ps1 [-Reseed]      # create it (no-op if it exists)
#     . "$PSScriptRoot\pcsx2-auto.ps1"; $dp = Initialize-Pcsx2Auto   # from another script
# PCSX2 keeps its data in <datapath>\PCSX2 (inis, memcards, sstates, logs...).
# Then: PCSX2\pcsx2-qt.exe -datapath <full path of local\pcsx2-auto> -batch ... -- disc\srwz.iso
# Seeded from Wyn's Documents\PCSX2\inis\PCSX2.ini (copied once; later changes to his are
# not followed unless -Reseed) with:
#   [Folders] Bios = his BIOS folder as an absolute path; every other folder (memcards,
#     sstates, logs, snaps, cache...) is relative, so it lands inside local\pcsx2-auto.
#   [UI] ConfirmShutdown = false (no "Confirm Shutdown" prompt), no pause on focus loss.
#   [AutoUpdater] CheckAtStartup = false (no update dialog).
#   [EmuCore] EnablePINE = true, PINESlot = 28011 (tools\pine.py).
#   [SPU2/Output] Backend = Null (PCSX2 2.8's "Null (No Output)"): no audio device and no
#     audio session at all, so a scripted run is silent and does not switch Wyn's sound
#     devices on and off. Set again before every launch (Set-Pcsx2AutoAudio, also on an existing
#     folder); -Sound on tools\pcsx2-capture.ps1 puts back his own backend (Cubeb if unset).
#   [Pad1] keyboard keys next to his SDL controller bindings, as repeated keys (PCSX2 reads
#     a repeated key as an alternative binding; "A & B" would be a chord):
#     START=Return CROSS=X CIRCLE=C TRIANGLE=V SQUARE=Z UP/DOWN/LEFT/RIGHT=arrows
#     (none of these is a PCSX2 hotkey in [Hotkeys]).
# His memory cards (memcards\*.ps2) are copied in, so the game sees the same saves.
# Settings PCSX2 itself rewrites on exit (window geometry...) only change this copy.
param([switch]$Reseed)

$script:Pcsx2AutoKeys = [ordered]@{ Up = "Up"; Right = "Right"; Down = "Down"; Left = "Left";
    Triangle = "V"; Circle = "C"; Cross = "X"; Square = "Z"; Start = "Return";
    L1 = "Q"; R1 = "W"; L2 = "A"; R2 = "S"; Select = "Backspace" }

function Set-IniValue([string[]]$lines, [string]$section, [string]$key, [string]$value) {
    $out = New-Object Collections.Generic.List[string]
    $sec = ""; $done = $false; $seen = $false
    foreach ($l in $lines) {
        if ($l -match '^\s*\[(.+)\]\s*$') {
            if ($sec -eq $section -and -not $done) { $out.Add("$key = $value"); $done = $true }
            $sec = $Matches[1]; if ($sec -eq $section) { $seen = $true }
        } elseif ($sec -eq $section -and $l -match "^\s*$([regex]::Escape($key))\s*=") {
            if (-not $done) { $out.Add("$key = $value"); $done = $true }
            continue
        }
        $out.Add($l)
    }
    if (-not $done) {
        if (-not $seen) { $out.Add(""); $out.Add("[$section]") }
        $out.Add("$key = $value")
    }
    , $out.ToArray()
}

# Values PCSX2 2.8 reads for [SPU2/Output] Backend: Null, Cubeb, SDL.
function Get-Pcsx2UserAudioBackend {
    $his = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "PCSX2\inis\PCSX2.ini"
    $b = "Cubeb"; $sec = ""
    if (Test-Path $his) {
        foreach ($l in Get-Content $his) {
            if ($l -match '^\s*\[(.+)\]\s*$') { $sec = $Matches[1] }
            elseif ($sec -eq "SPU2/Output" -and $l -match '^\s*Backend\s*=\s*(\S+)\s*$' -and $Matches[1] -ne "Null") { $b = $Matches[1] }
        }
    }
    $b
}

# Sets the private config's audio backend: Null (no device, the default) or, with -Sound, his own.
function Set-Pcsx2AutoAudio([string]$dataPath, [switch]$Sound) {
    $ini = Join-Path $dataPath "PCSX2\inis\PCSX2.ini"
    if (-not (Test-Path $ini)) { return }
    $backend = if ($Sound) { Get-Pcsx2UserAudioBackend } else { "Null" }
    $lines = Set-IniValue (Get-Content $ini) "SPU2/Output" "Backend" $backend
    [IO.File]::WriteAllLines($ini, $lines)
}

function Initialize-Pcsx2Auto([switch]$Reseed) {
    $root = Split-Path -Parent $PSScriptRoot
    $dp = Join-Path $root "local\pcsx2-auto"
    $data = Join-Path $dp "PCSX2"   # PCSX2 keeps its data in <datapath>\PCSX2
    $ini = Join-Path $data "inis\PCSX2.ini"
    if ((Test-Path $ini) -and -not $Reseed) { Set-Pcsx2AutoAudio $dp; return $dp }
    $his = Join-Path ([Environment]::GetFolderPath("MyDocuments")) "PCSX2"
    New-Item -ItemType Directory -Force (Join-Path $data "inis"), (Join-Path $data "memcards") | Out-Null
    $lines = Get-Content (Join-Path $his "inis\PCSX2.ini")
    # Drop any keyboard pad binding already there (ours, from earlier), then add ours.
    $out = New-Object Collections.Generic.List[string]; $sec = ""
    foreach ($l in $lines) {
        if ($l -match '^\s*\[(.+)\]\s*$') { $sec = $Matches[1] }
        if ($sec -eq "Pad1" -and $l -match '^\s*\w+\s*=\s*Keyboard/') { continue }
        $out.Add($l)
        if ($sec -eq "Pad1" -and $l -match '^\s*(\w+)\s*=\s*SDL-' -and $script:Pcsx2AutoKeys.Contains($Matches[1])) {
            $out.Add(("{0} = Keyboard/{1}" -f $Matches[1], $script:Pcsx2AutoKeys[$Matches[1]]))
        }
    }
    $lines = $out.ToArray()
    $bios = (Select-String -InputObject ($lines -join "`n") -Pattern '(?m)^\s*Bios\s*=\s*(.+?)\s*$').Matches
    $biosDir = if ($bios.Count) { $bios[0].Groups[1].Value } else { "bios" }
    if (-not [IO.Path]::IsPathRooted($biosDir)) { $biosDir = [IO.Path]::GetFullPath((Join-Path $his $biosDir)) }
    $lines = Set-IniValue $lines "Folders" "Bios" $biosDir
    $lines = Set-IniValue $lines "UI" "ConfirmShutdown" "false"
    $lines = Set-IniValue $lines "UI" "PauseOnFocusLoss" "false"
    $lines = Set-IniValue $lines "UI" "StartFullscreen" "false"
    $lines = Set-IniValue $lines "UI" "SetupWizardIncomplete" "false"
    $lines = Set-IniValue $lines "AutoUpdater" "CheckAtStartup" "false"
    $lines = Set-IniValue $lines "EmuCore" "EnablePINE" "true"
    $lines = Set-IniValue $lines "EmuCore" "PINESlot" "28011"
    $lines = Set-IniValue $lines "SPU2/Output" "Backend" "Null"
    [IO.File]::WriteAllLines($ini, $lines)
    Get-ChildItem (Join-Path $his "memcards") -Filter *.ps2 -ErrorAction SilentlyContinue |
        ForEach-Object { Copy-Item $_.FullName (Join-Path $data "memcards") -Force }
    $dp
}

if ($MyInvocation.InvocationName -ne ".") { Initialize-Pcsx2Auto -Reseed:$Reseed }
