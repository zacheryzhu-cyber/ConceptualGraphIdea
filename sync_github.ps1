# Commit and push any changes to GitHub (origin/main). Safe to run any time; does nothing when nothing changed.
# Usage:  .\sync_github.ps1 ["commit message"]
param([string]$Message = "")

$env:Path += ";$env:LOCALAPPDATA\Programs\MinGit\cmd;$env:LOCALAPPDATA\Programs\gh\bin"
Set-Location $PSScriptRoot

git add -A 2>$null
$staged = @(git diff --cached --name-only)
if ($staged.Count -eq 0) { Write-Host "GitHub sync: nothing to commit."; exit 0 }

# guard: files over 100 MB are rejected by GitHub unless they are Git LFS pointers (*.pth is tracked by LFS)
$bad = $staged | Where-Object {
    (Test-Path -LiteralPath $_) -and (Get-Item -LiteralPath $_).Length -gt 90MB -and
    -not ((git check-attr filter -- $_) -match 'filter: lfs')
}
if ($bad) {
    git reset -q
    Write-Host "GitHub sync ABORTED, too large for GitHub: $($bad -join ', '). Track it with 'git lfs track' or ignore it."
    exit 1
}

if (-not $Message) {
    $names = ($staged | Select-Object -First 4 | ForEach-Object { Split-Path $_ -Leaf }) -join ", "
    $more = if ($staged.Count -gt 4) { " +$($staged.Count - 4) more" } else { "" }
    $Message = "Update $names$more"
}
git commit -q -m $Message 2>$null
git push -q origin main 2>$null
if ($LASTEXITCODE -eq 0) { Write-Host "GitHub sync: pushed '$Message'." } else { Write-Host "GitHub sync: commit saved locally, push failed (check gh auth status)."; exit 1 }
