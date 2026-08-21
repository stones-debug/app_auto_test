; Inno Setup 安装脚本（Windows 方案 §4.2）
; 用法: iscc packaging/setup.iss /DAppVersion=1.1.0 /DOutputDir=dist
; 产物: app-auto-test-agent-<version>-windows-x64-setup.exe

#ifndef AppVersion
  #define AppVersion "1.1.0"
#endif
#ifndef OutputDir
  #define OutputDir "dist"
#endif
#ifndef SourceDir
  #define SourceDir "dist\app-auto-test-agent"
#endif

#define AppName "APP自动化测试平台 Agent"
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
Name: "chinesesimplified"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "autostart"; Description: "登录 Windows 后自动启动 Agent"; GroupDescription: "启动选项:"

[Files]
Source: "{#SourceDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"
Name: "{autoprograms}\{#AppName} 卸载"; Filename: "{uninstallexe}"

[Registry]
; 当前用户登录自启动（HKCU，无需管理员权限）
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; ValueType: string; \
  ValueName: "AppAutoTestAgent"; ValueData: """{app}\{#AppExeName}"" --desktop"; \
  Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExeName}"; Parameters: "--desktop"; Description: "启动 Agent"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: filesandordirs; Name: "{localappdata}\AppAutoTestAgent\logs"
Type: filesandordirs; Name: "{localappdata}\AppAutoTestAgent\credentials"
