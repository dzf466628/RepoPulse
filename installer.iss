#define AppName "RepoPulse"
#define AppVersion "0.2.0"
#define AppPublisher "RepoPulse"
#define AppExeName "RepoPulse.exe"

[Setup]
AppId={{A7F9B5E8-2B2D-4B4A-A7B1-0E2D7D1A4C20}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/dzf466628/RepoPulse
DefaultDirName={localappdata}\Programs\RepoPulse
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
OutputDir=dist
OutputBaseFilename=RepoPulse-Setup-v{#AppVersion}
SetupIconFile=RepoPulse.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma
SolidCompression=yes
WizardStyle=modern
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
VersionInfoVersion={#AppVersion}.0
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=RepoPulse Git 状态管理工具安装程序
VersionInfoProductName={#AppName}

[Files]
Source: "dist\RepoPulse\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall skipifsilent
