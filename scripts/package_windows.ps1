param(
    [string]$OutputDirectory = "artifacts"
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $PSScriptRoot
$distributionDirectory = Join-Path $projectRoot "dist\main"
$archiveDirectory = Join-Path $projectRoot $OutputDirectory
$archivePath = Join-Path $archiveDirectory "embedded-flash-assistant-windows-x64.zip"

Push-Location $projectRoot
try {
    if (-not [Environment]::Is64BitProcess) {
        throw "Windows packaging requires a 64-bit Python interpreter."
    }

    $sourceJLinkLibrary = Join-Path $projectRoot "bin\win\JLink_x64.dll"
    if (-not (Test-Path -LiteralPath $sourceJLinkLibrary -PathType Leaf)) {
        throw "Missing required J-Link runtime: $sourceJLinkLibrary"
    }

    python -m PyInstaller --clean --noconfirm main.spec
    if ($LASTEXITCODE -ne 0) {
        throw "PyInstaller exited with code $LASTEXITCODE."
    }

    $requiredFiles = @(
        (Join-Path $distributionDirectory "main.exe"),
        (Join-Path $distributionDirectory "_internal\bin\win\JLink_x64.dll"),
        (Join-Path $distributionDirectory "_internal\bin\ST\Devices.xml"),
        (Join-Path $distributionDirectory "_internal\bin\ST\W25Q16_STM32L4xx.elf"),
        (Join-Path $distributionDirectory "_internal\logo.svg")
    )
    foreach ($requiredFile in $requiredFiles) {
        if (-not (Test-Path -LiteralPath $requiredFile -PathType Leaf)) {
            throw "Package validation failed; missing file: $requiredFile"
        }
    }

    New-Item -ItemType Directory -Path $archiveDirectory -Force | Out-Null
    Compress-Archive `
        -Path $distributionDirectory `
        -DestinationPath $archivePath `
        -CompressionLevel Optimal `
        -Force

    Write-Host "Windows package created: $archivePath"
}
finally {
    Pop-Location
}
