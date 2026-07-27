param(
    [int]$Port = 8765
)

$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$pythonExe = Join-Path $projectRoot ".venv\Scripts\python.exe"
$contentRoot = Join-Path $projectRoot "Physics3Split"

if (-not (Test-Path -LiteralPath $pythonExe -PathType Leaf)) {
    throw "Python environment not found at $pythonExe. Run the installation steps in README.md first."
}
if (-not (Test-Path -LiteralPath $contentRoot -PathType Container)) {
    throw "Prepared Physics 3 folder not found at $contentRoot."
}

$env:PYTHONPATH = Join-Path $projectRoot "src"
Write-Host "Serving the prepared Physics 3 collection from $contentRoot"
Write-Host "Open http://127.0.0.1:$Port"
& $pythonExe -m pdf_splitter.study_server `
    --root $contentRoot `
    --port $Port `
    --title "Physics 3" `
    --storage-key "physics3-study-progress"
