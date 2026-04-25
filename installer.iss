; Inno Setup script for voice-cmds v0.0.1
; Build with:  iscc installer.iss
; Output:      release/voice-cmds-Setup-v0.0.1.exe

#define AppName        "voice-cmds"
#define AppVersion     "0.0.1"
#define AppPublisher   "erichuanp"
#define AppURL         "https://github.com/erichuanp/voice-cmds"
#define AppExeName     "voice-cmds.exe"

[Setup]
AppId={{6F2D3A8C-7E11-4B59-9C3D-D8F6E5B27A91}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL={#AppURL}
AppSupportURL={#AppURL}/issues
AppUpdatesURL={#AppURL}/releases
DefaultDirName={localappdata}\Programs\voice-cmds
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
PrivilegesRequiredOverridesAllowed=dialog
OutputDir=release
OutputBaseFilename=voice-cmds-Setup-v{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
UninstallDisplayName={#AppName} {#AppVersion}
SetupLogging=yes

[Languages]
Name: "english";       MessagesFile: "compiler:Default.isl"
Name: "chinesesimp";   MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[Tasks]
Name: "desktopicon";   Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked
Name: "autostart";     Description: "{code:AutostartLabel}"; GroupDescription: "{code:AutostartGroup}"; Flags: unchecked

[Files]
; Pull the entire PyInstaller --onedir output
Source: "dist\voice-cmds\voice-cmds.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "dist\voice-cmds\_internal\*";    DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Writable user data dirs alongside the exe (configs ship from _internal/config)
Name: "{app}\config";   Permissions: users-modify
Name: "{app}\models";   Permissions: users-modify
Name: "{app}\logs";     Permissions: users-modify
Name: "{app}\scripts";  Permissions: users-modify

[Icons]
Name: "{group}\{#AppName}";        Filename: "{app}\{#AppExeName}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"
Name: "{userdesktop}\{#AppName}";  Filename: "{app}\{#AppExeName}"; Tasks: desktopicon

[Registry]
; Optional autostart (HKCU per-user, no admin needed)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; ValueName: "voice-cmds"; ValueData: """{app}\{#AppExeName}"""; Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{app}\models"
Type: filesandordirs; Name: "{app}\logs"

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
