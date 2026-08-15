# build_release.ps1 [-Version "0.5.2"] [-DistDir "dist"]
# Packages <DistDir>/voice-cmds/ into:
#   release/voice-cmds-v<Version>-portable.zip   (7-Zip)
#   release/voice-cmds-Setup-v<Version>.exe      (Inno Setup — mature wizard,
#     per-user install, desktop shortcut, uninstaller)
param(
    [string]$Version = "0.5.2",
    [string]$DistDir = "dist"
)

$ErrorActionPreference = "Stop"

$Root    = $PSScriptRoot
$Dist    = Join-Path $Root "$DistDir\voice-cmds"
$Out     = Join-Path $Root "release"
$Zip     = Join-Path $Out "voice-cmds-v$Version-portable.zip"
$Sevenz  = "C:\Program Files\7-Zip\7z.exe"
$Iscc    = "C:\Program Files (x86)\Inno Setup 6\ISCC.exe"

if (!(Test-Path $Dist))   { throw "Build output missing: $Dist (run pyinstaller first)" }
if (!(Test-Path $Sevenz)) { throw "7-Zip not found at $Sevenz" }
if (!(Test-Path $Iscc))   { throw "Inno Setup not found at $Iscc" }
if (!(Test-Path $Out))    { New-Item -ItemType Directory -Path $Out | Out-Null }

# 1) Portable zip
Write-Host "[1/3] Building portable zip..."
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Push-Location (Split-Path $Dist -Parent)
try {
    & $Sevenz a -tzip -mx=9 $Zip "voice-cmds" | Out-Null
} finally {
    Pop-Location
}
$ZipSize = (Get-Item $Zip).Length / 1MB
Write-Host ("    -> {0}  ({1:N1} MB)" -f $Zip, $ZipSize)

# 2) Inno Setup installer (mature wizard, per-user, uninstaller included)
Write-Host "[2/3] Building installer (Inno Setup)..."
& $Iscc "/DAppVersion=$Version" (Join-Path $Root "installer.iss") | Out-Null
$Setup = Join-Path $Out "voice-cmds-Setup-v$Version.exe"
if (!(Test-Path $Setup)) { throw "Inno Setup failed — no $Setup produced" }
$SetupSize = (Get-Item $Setup).Length / 1MB
Write-Host ("    -> {0}  ({1:N1} MB)" -f $Setup, $SetupSize)

Write-Host "[3/3] Done. Artifacts in: $Out"
