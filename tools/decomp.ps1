# Decompiles the functions containing the given addresses (Ghidra headless, read-only) and
# writes the clean output (no Ghidra chatter) to local\runs\<name>.txt, then prints it.
#     powershell -File tools\decomp.ps1 -Name decomp18 0x18b260 0x18bd70
param([Parameter(Mandatory = $true)][string]$Name, [Parameter(ValueFromRemainingArguments = $true)][string[]]$Addresses)
$root = Split-Path -Parent $PSScriptRoot
$raw = Join-Path $root "local\runs\$Name.raw.txt"
$out = Join-Path $root "local\runs\$Name.txt"
$headless = Join-Path $root "ghidra\support\analyzeHeadless.bat"
$project = Join-Path $root "local\ghidra"
$scripts = Join-Path $root "tools\ghidra"
. (Join-Path $PSScriptRoot "launch.ps1")  # Invoke-Quiet: no console window for Ghidra/java
$null = Invoke-Quiet $headless (@($project, "srwz", "-process", "SLPS_258.87", "-noanalysis", "-readOnly", "-scriptPath", $scripts, "-postScript", "Decompile.java") + $Addresses) -StdOut $raw -MergeErr
$lines = Get-Content $raw
$keep = New-Object System.Collections.Generic.List[string]
$inScript = $false
foreach ($line in $lines) {
    if ($line -match "Decompile.java> ==") { $inScript = $true }
    if ($line -match "^INFO  (REPORT|ANALYZING|Deleting|Closing|HEADLESS: execution complete)") { $inScript = $false }
    if ($line -match "^ERROR|Exception") { $keep.Add($line); continue }
    if ($inScript) { $clean = ($line -replace "^INFO  Decompile.java> ", "") -replace " \(GhidraScript\)\s*$", ""; $keep.Add($clean) }
}
$keep | Set-Content -Encoding utf8 $out
Remove-Item $raw
Get-Content $out
