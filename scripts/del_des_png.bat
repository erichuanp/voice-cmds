@echo off
REM Example user script: move all .png files from Desktop to Recycle Bin
REM Triggered by voice command "吃饭" (per config/commands.json)
powershell -NoProfile -Command "Add-Type -AssemblyName Microsoft.VisualBasic; Get-ChildItem -Path ([Environment]::GetFolderPath('Desktop')) -Filter *.png | ForEach-Object { [Microsoft.VisualBasic.FileIO.FileSystem]::DeleteFile($_.FullName, 'OnlyErrorDialogs', 'SendToRecycleBin') }"
