param(
    [string]$Remote = "https://github.com/YehudaShani/TestPrep.git",
    [string]$Branch = "gh-pages",
    [switch]$SkipBuild,
    [switch]$NoPush
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$siteDir = Join-Path $projectRoot "site"
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"

if (-not $SkipBuild) {
    if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
        throw "Python environment not found at $pythonExe. Run the installation steps in README.md first."
    }
    $env:PYTHONPATH = Join-Path $projectRoot "src"
    & $pythonExe -m pdf_splitter.build_site --out $siteDir
    if ($LASTEXITCODE -ne 0) { throw "site build failed" }
}

if (-not (Test-Path -LiteralPath $siteDir -PathType Container)) {
    throw "No site to deploy at $siteDir."
}

# GitHub Pages would otherwise run Jekyll over the rendered pages.
$noJekyll = Join-Path $siteDir ".nojekyll"
if (-not (Test-Path -LiteralPath $noJekyll)) {
    New-Item -ItemType File -Path $noJekyll | Out-Null
}

if (-not (Test-Path -LiteralPath (Join-Path $siteDir ".git") -PathType Container)) {
    git -C $siteDir init -q
    if (-not $?) { throw "could not create the deploy repository" }
}

# The site is a build artifact, so every deploy is one orphan commit: old
# renders never pile up in history and the repository stays about the size
# of the site itself. That is also why the push has to be a force push.
if (git -C $siteDir branch --list deploy-tmp) {
    git -C $siteDir branch -q -D deploy-tmp
}
git -C $siteDir checkout -q --orphan deploy-tmp
if (-not $?) { throw "could not start the deploy commit" }
git -C $siteDir add -A
if (-not $?) { throw "could not stage the site" }
git -C $siteDir commit -q -m ("Study site {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm"))
if (-not $?) { throw "could not create the deploy commit" }
git -C $siteDir branch -q -M $Branch
git -C $siteDir reflog expire --expire=now --all
git -C $siteDir gc -q --prune=now

$siteMb = [math]::Round(((Get-ChildItem -LiteralPath $siteDir -Recurse -File -Force |
    Where-Object { $_.FullName -notlike "*\.git\*" } |
    Measure-Object -Property Length -Sum).Sum / 1MB), 1)
$repoMb = [math]::Round(((Get-ChildItem -LiteralPath (Join-Path $siteDir ".git") -Recurse -File -Force |
    Measure-Object -Property Length -Sum).Sum / 1MB), 1)
Write-Host "Site $siteMb MB, deploy repository $repoMb MB"

if ($NoPush) {
    Write-Host "Prepared but not pushed. Run: git -C `"$siteDir`" push --force $Remote $Branch"
    return
}

git -C $siteDir push --force $Remote $Branch
if (-not $?) { throw "push failed" }
Write-Host "Deployed to $Remote ($Branch)"
