# Missing guest functions from a runner's stderr, for tools\run-report.ps1 and tools\wall.ps1
# (dot-source it). The runtime logs one `[guest-branch:missing-target] ... source=0x.. target=0x..
# pc=0x.. ra=0x.. ... thread=N sourceName=.. raName=.. trace=..` line and parks the thread
# quietly; these helpers make that line impossible to miss in a report.
#     . tools\missingtargets.ps1
#     $m = Get-MissingTargets $errLines        # one object per distinct target
#     $m | ForEach-Object { Format-MissingTarget $_ }
#     Write-MissingTargets $m                    # red on the console
# thread=/sourceName=/raName= come from runtimes built after PS2Recomp srwz-missing; older
# lines print without them.

$MissingTargetHint = "add to config/entries.txt via tools/codeptrs.py, regenerate"

function Get-MissingTargets($lines) {
    $seen = [ordered]@{}
    foreach ($l in @($lines)) {
        if ($l -notmatch '\[guest-branch:missing-target\]') { continue }
        $f = @{}
        foreach ($m in [regex]::Matches($l, '(?<=^|\s)(\w+)=(\S+)')) { if (-not $f.ContainsKey($m.Groups[1].Value)) { $f[$m.Groups[1].Value] = $m.Groups[2].Value } }
        $t = $f['target']
        if (-not $t) { continue }
        if ($seen.Contains($t)) { $seen[$t].Count++; continue }
        $seen[$t] = [pscustomobject]@{
            Target = $t; Source = $f['source']; Pc = $f['pc']; Ra = $f['ra']; Kind = $f['kind']
            Thread = $f['thread']; SourceName = $f['sourceName']; RaName = $f['raName']; Count = 1
        }
    }
    @($seen.Values)
}

function Format-MissingTarget($m) {
    $named = { param($addr, $name) if ($name -and $name -ne $addr -and $name -notmatch '^0x[0-9a-f]+$') { "$addr ($name)" } else { "$addr" } }
    $from = if ($m.Source -and $m.Source -ne '0x0') { & $named $m.Source $m.SourceName } else { "$($m.Pc) (pc)" }
    "MISSING FUNCTION target=$($m.Target) from source/pc $from ra $(& $named $m.Ra $m.RaName) thread $(if ($m.Thread) { $m.Thread } else { '?' })" +
        "$(if ($m.Kind) { " ($($m.Kind))" }) -- $MissingTargetHint"
}

function Write-MissingTargets($missing) {
    foreach ($m in @($missing | Where-Object { $_ })) { Write-Host (Format-MissingTarget $m) -ForegroundColor Red }
}
