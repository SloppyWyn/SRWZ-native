# Review pack for a branch: everything a reviewer needs in one folder, plus a brief skeleton.
#     powershell -File tools\review-pack.ps1 -Branch srwz-foo [-Logs a.out.txt,b.txt] [-Base <rev>] [-Name dir]
#     powershell -File tools\review-pack.ps1 -Branch tooling -Repo main      (this repo, base main)
#     powershell -File tools\review-pack.ps1 -SelfTest
# -Repo runtime (default): third_party\PS2Recomp of the main checkout, base branch srwz;
# -Repo main: the main checkout, base branch main. -BaseBranch overrides the base branch,
# -Base an exact base commit. Without -Base the base is the merge base with the base branch;
# a branch already merged into it is reviewed against the base it was merged onto (the merge
# commit's first parent).
# Writes local\runs\<Name>\ (default Name = the branch, '/' -> '-'):
#     diff.patch    the diff base..branch          stat.txt    --stat
#     commits.txt   hash + subject, oldest first   tests.txt   test counts found
#     brief.md      review brief skeleton (branch, base, commits, files by area, test evidence,
#                   runtime-reviewer checklist)
# Test counts are scraped from the branch's notes files (NOTES-*.md, docs/*NOTES*.md; the ones the
# branch changes, else all of them) and from the -Logs files: lines such as "Passed: N",
# "differ 0", "flagged 0", "N tests passed", ctest summaries, "FAILED".
# Game-agnostic: only git and text. git runs through tools\launch.ps1 (Invoke-Quiet).
param([string]$Branch = "", [ValidateSet("runtime", "main")] [string]$Repo = "runtime",
      [string]$BaseBranch = "", [string]$Base = "", [string[]]$Logs = @(), [string]$Name = "",
      [switch]$SelfTest)
$ErrorActionPreference = "Stop"
. (Join-Path $PSScriptRoot "launch.ps1")
$root = $null
try {
    . (Join-Path $PSScriptRoot "gameconfig.ps1")
    $root = Get-MainRoot
} catch { }
if (-not $root) {
    # gameconfig.ps1 unavailable: the main checkout from git's common dir
    $common = @(Invoke-Quiet git @("-C", $PSScriptRoot, "rev-parse", "--path-format=absolute", "--git-common-dir"))
    $root = if ($LASTEXITCODE -eq 0 -and $common.Count) { Split-Path -Parent ($common[0].Trim() -replace '/', '\') } else { Split-Path -Parent $PSScriptRoot }
}
$runs = Join-Path $root "local\runs"
try { $r = Resolve-GamePath (Get-GameConfig) 'runs'; if ($r) { $runs = $r } } catch { }

function Git([string]$dir, [string[]]$a, [switch]$Soft) {
    $out = Invoke-Quiet git (@("-C", $dir, "-c", "core.quotepath=off") + $a)
    if (-not $Soft -and $LASTEXITCODE -ne 0) { throw "git $($a -join ' ') failed ($LASTEXITCODE)" }
    $out
}

$TestPattern = '(?i)(\bpass(ed)?\s*[:=]\s*\d+|\b\d+\s+(tests?\s+)?(passed|failed|differ|flagged)\b|\b(failed|differ|differs|differences|flagged|mismatch(es)?|errors?)\s*[:=]?\s*\d+\b|\btests? passed\b|\bFAILED\b|\bALL TESTS PASSED\b|\b\d+\s*/\s*\d+\s+(passed|ok)\b)'
$NotesPattern = '(^|/)NOTES-[^/]*\.md$|^docs/[^/]*NOTES[^/]*\.md$'

function Find-TestLines([string]$label, [string[]]$lines) {
    $hits = @()
    for ($i = 0; $i -lt $lines.Count; $i++) {
        if ($lines[$i] -match $TestPattern) { $hits += ("{0}:{1}: {2}" -f $label, ($i + 1), $lines[$i].Trim()) }
    }
    $hits
}

function Format-Code([string[]]$items) { ($items | ForEach-Object { '`' + $_ + '`' }) -join ', ' }

function Get-Area([string]$path) {
    $d = $path -split '/'
    if ($d.Count -le 1) { return "(root)" }
    if ($d.Count -eq 2) { return $d[0] }
    "$($d[0])/$($d[1])"
}

function New-ReviewPack([string]$Branch, [string]$Repo, [string]$BaseBranch, [string]$Base, [string[]]$Logs, [string]$Name) {
    $dir = if ($Repo -eq "main") { $root } else { Join-Path $root "third_party\PS2Recomp" }
    if (-not $BaseBranch) { $BaseBranch = if ($Repo -eq "main") { "main" } else { "srwz" } }
    if (-not $Branch) { throw "-Branch is required" }
    $tip = @(Git $dir @("rev-parse", "--verify", "$Branch^{commit}"))[0].Trim()
    $baseNote = ""
    if (-not $Base) {
        $mb = @(Git $dir @("merge-base", $BaseBranch, $tip))[0].Trim()
        if ($mb -eq $tip) {
            # already merged. 1) the base branch's reflog: the newest entry that does not contain
            # the tip is the base branch just before the merge (fast-forward or merge commit)
            $pre = $null
            foreach ($e in @(Git $dir @("reflog", "show", "--format=%H", "refs/heads/$BaseBranch") -Soft | Where-Object { $_ })) {
                $null = Git $dir @("merge-base", "--is-ancestor", $tip, $e) -Soft
                if ($LASTEXITCODE -ne 0) { $pre = $e; break }
            }
            # 2) the merge commit that brought it in (first parent without the tip)
            $m = @()
            if (-not $pre) {
                $m = Git $dir @("rev-list", "--ancestry-path", "--merges", "--reverse", "$tip..$BaseBranch")
                $m = @($m | Where-Object { $_ } | Where-Object {
                    $null = Git $dir @("merge-base", "--is-ancestor", $tip, "$_^1") -Soft; $LASTEXITCODE -ne 0 } | Select-Object -First 1)
            }
            if ($pre) {
                $mb = @(Git $dir @("merge-base", $pre, $tip))[0].Trim()
                $baseNote = " (branch already merged into ${BaseBranch}; base = merge base with ${BaseBranch} before the merge, $($pre.Substring(0,9)), per reflog)"
            } elseif ($m.Count -gt 0) {
                $mb = @(Git $dir @("merge-base", "$($m[0])^1", $tip))[0].Trim()
                $baseNote = " (branch already merged into $BaseBranch by $($m[0].Substring(0,9)); base = merge base with its first parent)"
            } else {
                # fast-forwarded in: fall back to the commit the branch was created from (reflog)
                $rl = @(Git $dir @("reflog", "show", "--format=%H %gs", "refs/heads/$Branch") -Soft | Where-Object { $_ })
                $created = if ($rl.Count) { $rl[-1] } else { "" }
                if ($created -match "^([0-9a-f]{40}) branch: Created from") {
                    $mb = $Matches[1]
                    $baseNote = " (branch already fast-forwarded into ${BaseBranch}; base = the commit it was created from, per reflog)"
                } else { $baseNote = " (branch tip is already in ${BaseBranch}: nothing to review; pass -Base)" }
            }
        }
        $Base = $mb
    } else { $Base = @(Git $dir @("rev-parse", "--verify", "$Base^{commit}"))[0].Trim(); $baseNote = " (given)" }

    if (-not $Name) { $Name = $Branch -replace '[\\/:*?"<>|]', '-' }
    $out = Join-Path $runs $Name
    New-Item -ItemType Directory -Force $out | Out-Null
    $f = @{ diff = "$out\diff.patch"; stat = "$out\stat.txt"; commits = "$out\commits.txt"; tests = "$out\tests.txt"; brief = "$out\brief.md" }

    $range = "$Base..$tip"
    $null = Git $dir @("diff", "--no-color", "--output=$($f.diff)", $range)
    $stat = Git $dir @("diff", "--no-color", "--stat=200", $range)
    Set-Content -Encoding utf8 $f.stat $stat
    $commits = @(Git $dir @("log", "--reverse", "--format=%h %s", $range) | Where-Object { $_ })
    Set-Content -Encoding utf8 $f.commits $commits
    $files = @(Git $dir @("diff", "--name-status", $range) | Where-Object { $_ })

    # test evidence
    $changed = @($files | ForEach-Object { ($_ -split "`t")[-1] })
    $notes = @($changed | Where-Object { $_ -match $NotesPattern -and $files -notmatch "^D`t$([regex]::Escape($_))$" })
    $notesScope = "changed by the branch"
    if ($notes.Count -eq 0) {
        $notes = @(Git $dir @("ls-tree", "-r", "--name-only", $tip) | Where-Object { $_ -match $NotesPattern })
        $notesScope = "none changed; all in the branch tree"
    }
    $tests = @()
    foreach ($n in $notes) {
        $txt = Git $dir @("show", "${tip}:$n") -Soft
        if ($LASTEXITCODE -eq 0) { $tests += Find-TestLines $n $txt }
    }
    foreach ($l in $Logs) {
        if (Test-Path -LiteralPath $l) { $tests += Find-TestLines $l (Get-Content -LiteralPath $l) }
        else { $tests += "${l}: (log file not found)" }
    }
    Set-Content -Encoding utf8 $f.tests $tests

    # brief
    $b = New-Object System.Collections.Generic.List[string]
    $b.Add("# Review brief: $Branch"); $b.Add("")
    $b.Add("- Repo: $Repo ($dir)")
    $b.Add("- Branch: ``$Branch`` at ``$($tip.Substring(0,9))``")
    $b.Add("- Base: ``$($Base.Substring(0,9))`` vs ``$BaseBranch``$baseNote")
    $b.Add("- Summary: $($stat | Select-Object -Last 1)")
    $b.Add("- Pack: diff.patch, stat.txt, commits.txt, tests.txt"); $b.Add("")
    $b.Add("## Purpose"); $b.Add(""); $b.Add("_(what the branch does and why; fill in)_"); $b.Add("")
    $b.Add("## Commits ($($commits.Count))"); $b.Add("")
    foreach ($c in $commits) { $b.Add("- $c") }
    $b.Add("")
    $b.Add("## Files changed by area ($($files.Count))"); $b.Add("")
    $groups = $files | Group-Object { Get-Area (($_ -split "`t")[-1]) } | Sort-Object Name
    foreach ($g in $groups) {
        $b.Add("### $($g.Name)")
        foreach ($x in $g.Group) { $p = $x -split "`t"; $b.Add("- $($p[0].Substring(0,1)) ``$($p[1..($p.Count-1)] -join ' -> ')``") }
        $b.Add("")
    }
    $b.Add("## Test evidence found"); $b.Add("")
    $nl = if ($notes.Count) { Format-Code $notes } else { "none" }
    $b.Add("Notes scanned (${notesScope}): $nl")
    if ($Logs.Count) { $b.Add("Logs scanned: " + (Format-Code $Logs)) }
    $b.Add("")
    if ($tests.Count) { foreach ($t in $tests) { $b.Add("- $t") } } else { $b.Add("- none found: ask for test output") }
    $b.Add("")
    $b.Add("## Reviewer checklist"); $b.Add("")
    foreach ($q in @(
        "ABI / arguments: new or changed syscalls, stubs and handlers read arguments per the EE convention (a0-a3, t0-t3; 64-bit and float args; return in v0/v1) and preserve what the caller expects",
        "Game-agnostic: no game names, addresses, CRCs or hard-coded paths in the runtime; game specifics go to config",
        "Default off: new behaviour sits behind a switch (env var / option) that defaults to off",
        "Switch off = byte-identical: with the switch off, behaviour (output, timing, logs) is unchanged from the base",
        "Thread safety: shared state touched from the guest, host and audio/IO threads is locked or atomic; no new races on hot paths",
        "Snapshot coverage: new runtime state is saved and restored by snapshots (or documented as not needed)",
        "Tests added: unit or regression tests cover the change and the counts above match the notes",
        "Notes / docs updated; no stray debug output, files or switches left on")) { $b.Add("- [ ] $q") }
    $b.Add(""); $b.Add("## Findings"); $b.Add(""); $b.Add("_(fill in)_")
    Set-Content -Encoding utf8 $f.brief $b

    [pscustomobject]@{ Out = $out; Tip = $tip; Base = $Base; Commits = $commits.Count; Files = $files.Count; Tests = $tests.Count; Paths = $f }
}

function Show-Pack($r) {
    Write-Host ("review pack: {0} commits, {1} files, {2} test lines (base {3})" -f $r.Commits, $r.Files, $r.Tests, $r.Base.Substring(0, 9))
    foreach ($k in 'brief', 'diff', 'stat', 'commits', 'tests') { Write-Host ("  {0,-8} {1}" -f $k, $r.Paths[$k]) }
}

if ($SelfTest) {
    $fail = 0
    # an unmerged runtime branch (ahead of srwz), else srwz-typed2; srwz-typed2 (merged) is checked too
    $rt = Join-Path $root "third_party\PS2Recomp"
    $tb = $Branch
    if (-not $tb) {
        foreach ($c in @(Git $rt @("for-each-ref", "--sort=-committerdate", "--format=%(refname:short)", "refs/heads/srwz-*"))) {
            if ([int](@(Git $rt @("rev-list", "--count", "srwz..$c"))[0]) -gt 0) { $tb = $c; break }
        }
    }
    if (-not $tb) { $tb = "srwz-typed2" }
    Write-Host "self-test branch: $tb"
    $log = Join-Path ([IO.Path]::GetTempPath()) "review-pack-selftest.log"
    Set-Content -Encoding utf8 $log @("noise", "Passed: 12", "images differ 0, flagged 0", "100% tests passed, 0 tests failed out of 7")
    $r = New-ReviewPack $tb "runtime" "" "" @($log) "selftest-review-pack"
    Show-Pack $r
    $check = {
        param($ok, $what) if ($ok) { Write-Host "  ok   $what" } else { Write-Host "  FAIL $what"; $script:fail++ }
    }
    & $check ($r.Base -ne $r.Tip) "base differs from tip"
    & $check ($r.Commits -gt 0) "commits listed ($($r.Commits))"
    & $check ($r.Files -gt 0) "files changed ($($r.Files))"
    & $check ((Get-Item $r.Paths.diff).Length -gt 0) "diff.patch non-empty"
    $tl = Get-Content $r.Paths.tests
    & $check (@($tl | Where-Object { $_ -like "*Passed: 12*" }).Count -eq 1) "log 'Passed: 12' found"
    & $check (@($tl | Where-Object { $_ -like "*differ 0, flagged 0*" }).Count -eq 1) "log 'differ 0, flagged 0' found"
    & $check (@($tl | Where-Object { $_ -like "*noise*" }).Count -eq 0) "noise line skipped"
    $mg = New-ReviewPack "srwz-typed2" "runtime" "" "" @() "selftest-review-pack-merged"
    & $check ($mg.Base -ne $mg.Tip -and $mg.Commits -gt 0) "merged branch srwz-typed2: base found ($($mg.Commits) commits)"
    $br = Get-Content -Raw $r.Paths.brief
    & $check ($br -match '## Files changed by area' -and $br -match 'Snapshot coverage' -and $br -match 'EE convention') "brief sections present"
    $m = New-ReviewPack "HEAD" "main" "" "HEAD~1" @() "selftest-review-pack-main"
    & $check ($m.Commits -ge 1 -and $m.Files -ge 1) "-Repo main with -Base HEAD~1: $($m.Commits) commit(s), $($m.Files) file(s)"
    foreach ($d in @($m.Out, $mg.Out)) {
        if ($d -and (Split-Path -Leaf $d) -like "selftest-review-pack-*") { Remove-Item -LiteralPath $d -Recurse -Force -ErrorAction SilentlyContinue }
    }
    Remove-Item -LiteralPath $log -Force -ErrorAction SilentlyContinue
    if ($fail) { Write-Host "SELFTEST FAILED ($fail)"; exit 1 }
    Write-Host "SELFTEST OK"; exit 0
}

Show-Pack (New-ReviewPack $Branch $Repo $BaseBranch $Base $Logs $Name)
