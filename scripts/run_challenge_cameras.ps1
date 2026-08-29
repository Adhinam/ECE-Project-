# Process each challenge camera in a separate process (resumable via .done markers).
$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$cameras = @(
    "store1:cam-zone-2",
    "store1:cam-entry",
    "store1:cam-billing",
    "store2:cam-entry-1",
    "store2:cam-entry-2",
    "store2:cam-zone",
    "store2:cam-billing"
)

foreach ($spec in $cameras) {
    Write-Host "=== Processing $spec ===" -ForegroundColor Cyan
    py -3 scripts/process_challenge_validation.py --only $spec
    if ($LASTEXITCODE -ne 0) {
        throw "Pipeline failed for $spec (exit $LASTEXITCODE)"
    }
}

Write-Host "=== Final ingest + reports ===" -ForegroundColor Cyan
py -3 scripts/process_challenge_validation.py --ingest-only
