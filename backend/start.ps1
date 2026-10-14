# Use the project's tested Windows entry point, without inherited reload sockets.
$ErrorActionPreference = 'Stop'
$backendPython = Join-Path $PSScriptRoot '.venv\Scripts\python.exe'
$backendExitCode = 0
Push-Location -LiteralPath $PSScriptRoot
try {
    if (Test-Path -LiteralPath $backendPython) {
        & $backendPython -m app.main
    } elseif (Get-Command uv -ErrorAction SilentlyContinue) {
        & uv run python -m app.main
    } else {
        throw 'Project Python was not found. Install dependencies with uv sync in the backend directory.'
    }
    $backendExitCode = $LASTEXITCODE
} finally {
    Pop-Location
}
exit $backendExitCode
