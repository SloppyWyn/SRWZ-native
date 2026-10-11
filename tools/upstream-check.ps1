# What landed upstream in PS2Recomp since our srwz branch forked, and where it touches files we
# changed too. Run at the start of a working day and before big runtime merges.
#     powershell -File tools\upstream-check.ps1 [-Branch srwz] [-Remote origin] [-NoFetch] [-SelfTest]
# Prints one line per upstream commit since the merge base: hash, date, subject, and
# "overlap: <files>" when it touches files our branch also changed since the merge base
# (those are the likely conflicts / things to port by hand), then the other upstream branches
# with their last commit date. "upstream: nothing new" when there is nothing.
param([string]$Branch = "srwz", [string]$Remote = "origin", [switch]$NoFetch, [switch]$SelfTest)

. "$PSScriptRoot\launch.ps1"
$root = Split-Path -Parent $PSScriptRoot
$common = @(Invoke-Quiet git @("-C", $root, "rev-parse", "--path-format=absolute", "--git-common-dir")) | Select-Object -First 1
if ($LASTEXITCODE -eq 0 -and $common) { $root = Split-Path -Parent $common.Trim() }
$sub = Join-Path $root "third_party\PS2Recomp"

function G { Invoke-Quiet git (@("-C", $sub) + [string[]]$args) }

function Get-UpstreamReport([string]$branch, [string]$remote, [switch]$noFetch) {
    $out = New-Object System.Collections.Generic.List[string]
    if (-not $noFetch) { G fetch --quiet $remote | Out-Null }
    $up = "$remote/main"
    $mb = (G merge-base $branch $up) | Select-Object -First 1
    if (-not $mb) { $out.Add("upstream: no merge base between $branch and $up"); return $out }
    $mbDate = (G log -1 "--format=%cd" "--date=short" $mb) | Select-Object -First 1
    $ours = @(G diff --name-only $mb $branch) | Where-Object { $_ }
    $commits = @(G log "--format=%h|%cd|%s" "--date=short" "$mb..$up") | Where-Object { $_ }
    $out.Add(("upstream {0} vs {1}: merge base {2} ({3}), {4} new upstream commit(s), {5} files changed on our side" -f $up, $branch, $mb.Substring(0, 9), $mbDate, $commits.Count, $ours.Count))
    if ($commits.Count -eq 0) { $out.Add("upstream: nothing new") }
    foreach ($c in $commits) {
        $p = $c -split '\|', 3
        $files = @(G diff-tree --no-commit-id --name-only -r $p[0]) | Where-Object { $_ }
        $overlap = @($files | Where-Object { $ours -contains $_ })
        $line = "  {0} {1} {2} ({3} files)" -f $p[0], $p[1], $p[2], $files.Count
        if ($overlap.Count) { $line += "  overlap: " + (($overlap | Select-Object -First 6) -join ", ") + $(if ($overlap.Count -gt 6) { " +$($overlap.Count - 6) more" } else { "" }) }
        $out.Add($line)
    }
    $refs = @(G for-each-ref "--format=%(refname:short) %(committerdate:short)" "refs/remotes/$remote") | Where-Object { $_ -and $_ -notmatch "^$remote(/HEAD)?\s" -and $_ -notmatch "^$remote/main\s" }
    if ($refs.Count) { $out.Add("other upstream branches: " + ($refs -join "; ")) }
    $out
}

if ($SelfTest) {
    $r = Get-UpstreamReport -branch $Branch -remote $Remote -noFetch
    $ok = ($r.Count -ge 2) -and ($r[0] -match "merge base [0-9a-f]{9}")
    $r | ForEach-Object { $_ }
    if ($ok) { "selftest PASSED"; exit 0 } else { "selftest FAILED"; exit 1 }
}
Get-UpstreamReport -branch $Branch -remote $Remote -noFetch:$NoFetch | ForEach-Object { $_ }
