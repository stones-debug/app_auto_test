; Inno Setup installer script (Windows plan 4.2)
; ASCII-only on purpose: Inno reads .iss as ANSI without BOM; keep this file ASCII.
; Usage: iscc packaging\setup.iss /DAppVersion=1.1.0 /DOutputDir=dist
; Output: app-auto-test-agent-<version>-windows-x64-setup.exe

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif
#ifndef SourceDir
  #define SourceDir "dist\app-auto-test-agent"
#endif

#define AppName "APP Auto Test Agent"
#define AppExeName "app-auto-test-agent.exe"

[Setup]
AppId={{8E3C0A1B-4F2A-4C1D-9B6A-2D3E5F7A9B0C}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=TL-Tek
DefaultDirName={localappdata}\AppAutoTestAgent
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir={#OutputDir}
OutputBaseFilename=app-auto-test-agent-{#AppVersion}-windows-x64-setup
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#AppExeName}
SetupLogging=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "Start Agent automatically after Windows login"; GroupDescription: "Startup options:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; Parameters: "--desktop"
Name: "{autoprograms}\{#AppName} Uninstall"; Filename: "{uninstallexe}"

[Registry]
; per-user autostart (HKCU, no admin rights)
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "AppAutoTestAgent"; ValueData: """{app}\{#AppExeName}"" --desktop"; \
  Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--desktop"; Description: "Start Agent"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\AppAutoTestAgent\logs"
Type: filesandordirs; Name: "{localappdata}\AppAutoTestAgent\credentials"
