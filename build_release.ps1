# build_release.ps1
# Packages dist/voice-cmds/ into:
#   release/voice-cmds-v0.0.1-portable.zip
#   release/voice-cmds-Setup-v0.0.1.exe   (7-Zip SFX self-extractor)

$ErrorActionPreference = "Stop"

$Version = "0.0.1"
$Root    = $PSScriptRoot
$Dist    = Join-Path $Root "dist\voice-cmds"
$Out     = Join-Path $Root "release"
$Zip     = Join-Path $Out "voice-cmds-v$Version-portable.zip"
$Sfx7z   = Join-Path $Out "voice-cmds-Setup-v$Version.exe"
$Sevenz  = "C:\Program Files\7-Zip\7z.exe"
$SfxMod  = "C:\Program Files\7-Zip\7z.sfx"

if (!(Test-Path $Dist))   { throw "Build output missing: $Dist (run pyinstaller first)" }
if (!(Test-Path $Sevenz)) { throw "7-Zip not found at $Sevenz" }
if (!(Test-Path $Out))    { New-Item -ItemType Directory -Path $Out | Out-Null }

# 1) Portable zip
Write-Host "[1/2] Building portable zip..."
if (Test-Path $Zip) { Remove-Item $Zip -Force }
Push-Location (Split-Path $Dist -Parent)
try {
    & $Sevenz a -tzip -mx=9 $Zip "voice-cmds" | Out-Null
} finally {
    Pop-Location
}
$ZipSize = (Get-Item $Zip).Length / 1MB
Write-Host ("    -> {0}  ({1:N1} MB)" -f $Zip, $ZipSize)

# 2) Self-extracting installer
Write-Host "[2/2] Building self-extracting installer..."
$TmpArchive = Join-Path $env:TEMP "voice-cmds-payload-$Version.7z"
if (Test-Path $TmpArchive) { Remove-Item $TmpArchive -Force }
Push-Location (Split-Path $Dist -Parent)
try {
    & $Sevenz a -t7z -mx=9 -ms=on $TmpArchive "voice-cmds" | Out-Null
} finally {
    Pop-Location
}

$SfxConfig = Join-Path $env:TEMP "sfx-config-$Version.txt"
@"
;!@Install@!UTF-8!
Title="voice-cmds v$Version Setup"
BeginPrompt="Install voice-cmds v$Version to the chosen folder?"
ExtractPathText="Install location"
ExtractPathTitle="voice-cmds v$Version"
GUIMode="1"
OverwriteMode="2"
Shortcut="Du,{voice-cmds\voice-cmds.exe},{voice-cmds.exe},{},{voice-cmds},{voice-cmds},{0}"
Shortcut="Pu,{voice-cmds\voice-cmds.exe},{voice-cmds.exe},{},{voice-cmds},{voice-cmds},{0}"
RunProgram="voice-cmds\voice-cmds.exe"
;!@InstallEnd@!
"@ | Set-Content -Encoding UTF8 -Path $SfxConfig

if (Test-Path $Sfx7z) { Remove-Item $Sfx7z -Force }
cmd /c "copy /b ""$SfxMod"" + ""$SfxConfig"" + ""$TmpArchive"" ""$Sfx7z""" | Out-Null
Remove-Item $TmpArchive -Force
Remove-Item $SfxConfig -Force

$SfxSize = (Get-Item $Sfx7z).Length / 1MB
Write-Host ("    -> {0}  ({1:N1} MB)" -f $Sfx7z, $SfxSize)

Write-Host ""
Write-Host "Done. Artifacts in: $Out"
Get-ChildItem $Out | Format-Table Name, @{n="MB";e={[math]::Round($_.Length/1MB,1)}} -AutoSize
