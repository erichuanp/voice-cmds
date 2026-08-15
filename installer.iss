; Inno Setup script for voice-cmds
; Build with:  iscc /DAppVersion=0.5.2 installer.iss
; Output:      release/voice-cmds-Setup-<version>.exe
;
; Per-user install — no admin rights needed anywhere:
;   - installs to %LOCALAPPDATA%\Programs\voice-cmds
;   - autostart is an HKCU Run value (the app can also toggle it in Settings)
;   - uninstaller removes the app, shortcuts, runtime data (models/logs/
;     config/scripts) and the autostart value.

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif

#define AppName        "voice-cmds"
#define ShortcutName   "Voice Commands"
#define AppPublisher   "erichuanp"
#define AppURL         "https://github.com/erichuanp/voice-cmds"
#define AppExeName     "voice-cmds.exe"

[Setup]
AppId={{6F2D3A8C-7E11-4B59-9C3D-D8F6E5B27A91}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\voice-cmds
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=release
OutputBaseFilename=voice-cmds-Setup-v{#AppVersion}
SetupIconFile=assets\app.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
SetupLogging=yes
LicenseFile=LICENSE
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english";       MessagesFile: "compiler:Default.isl"
Name: "chinesesimp";   MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon";   Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: checkedonce
Name: "autostart";     Description: "{code:AutostartLabel}"; GroupDescription: "{code:AutostartGroup}"; Flags: unchecked

[Files]
; Entire PyInstaller --onedir output
Source: "dist\voice-cmds\voice-cmds.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\voice-cmds\_internal\*";    DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Writable user-data dirs next to the exe (config ships from _internal/config)
Name: "{app}\config";   Permissions: users-modify
Name: "{app}\models";   Permissions: users-modify
Name: "{app}\logs";     Permissions: users-modify
Name: "{app}\scripts";  Permissions: users-modify

[Icons]
Name: "{group}\{#ShortcutName}";        Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"
Name: "{group}\{cm:UninstallProgram,{#AppName}}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#ShortcutName}";  Filename: "{app}\{#AppExeName}"; IconFilename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Optional per-user autostart, created on install only when the task is on.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "voice-cmds"; ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart
; Always delete the autostart value on uninstall — the app's own Settings
; dialog may have enabled it after installation (dontcreatekey = do not
; create it at install time, only clean it up at uninstall time).
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "voice-cmds"; ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue dontcreatekey

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
; Runtime data lives next to the exe and is not tracked by Inno — remove it
; explicitly so uninstalling really cleans everything.
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\config"
Type: filesandordirs; Name: "{app}\scripts"
Type: filesandordirs; Name: "{app}\_update"
Type: files; Name: "{app}\_update.json"
Type: files; Name: "{app}\update.bat"

[Code]
function AutostartLabel(Param: string): string;
begin
  if ActiveLanguage = 'chinesesimp' then
    Result := '开机自动启动'
  else
    Result := 'Launch automatically at login';
end;

function AutostartGroup(Param: string): string;
begin
  if ActiveLanguage = 'chinesesimp' then
    Result := '其他选项:'
  else
    Result := 'Additional options:';
end;
