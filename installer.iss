#define AppName "RepoPulse"
#ifndef AppVersion
#define AppVersion "1.0.16"
#endif
#define AppPublisher "RepoPulse"
#define AppExeName "RepoPulse.exe"

[Setup]
AppId={{A7F9B5E8-2B2D-4B4A-A7B1-0E2D7D1A4C20}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher={#AppPublisher}
AppPublisherURL=https://github.com/dzf466628/RepoPulse
DefaultDirName={code:GetDefaultDir}
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
; 自动更新静默升级：安装时强制关闭正在运行的旧版本（托盘程序拦截了普通关闭，
; 必须用 force 强杀，否则 /SUPPRESSMSGBOXES 下直接 Abort 回滚），完成后由 [Run] 拉起新版本
CloseApplications=force
RestartApplications=no
VersionInfoVersion={#AppVersion}.0
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=RepoPulse Git 状态管理工具安装程序
VersionInfoProductName={#AppName}

[Code]
function GetDefaultDir(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\RepoPulse'
  else
    Result := ExpandConstant('{localappdata}\Programs\RepoPulse');
end;

[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Files]
Source: "dist\RepoPulse\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
Type: files; Name: "{app}\_internal\icuuc.dll"
Type: files; Name: "{app}\_internal\icudt78.dll"

[Icons]
Name: "{autoprograms}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
; 安装完成后启动软件。不加 skipifsilent：自动更新走静默安装时也能自动重启新版本。
Filename: "{app}\{#AppExeName}"; Description: "启动 {#AppName}"; Flags: nowait postinstall runasoriginaluser
