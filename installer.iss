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
; 安装时写了用户 PATH，靠这条让正在运行的程序也收到"环境变量变了"的通知
ChangesEnvironment=yes
VersionInfoVersion={#AppVersion}.0
VersionInfoProductVersion={#AppVersion}
VersionInfoDescription=RepoPulse Git 状态管理工具安装程序
VersionInfoProductName={#AppName}

; 把内置 Git 的 cmd 目录登记进用户 PATH：这样命令行、AI 助手、IDE 也能直接用
; 这份 Git，用户不必另外装一个。默认勾选（checkedonce：用户上次取消过就记住不勾）。
[Tasks]
Name: "gitpath"; Description: "让命令行、AI 助手、IDE 也能使用自带的 Git（写入用户 PATH）"; GroupDescription: "附加选项："; Flags: checkedonce

[Registry]
Root: HKCU; Subkey: "Environment"; ValueType: expandsz; ValueName: "Path"; ValueData: "{olddata};{app}\_internal\git\cmd"; Tasks: gitpath; Check: NeedsGitPath(ExpandConstant('{app}\_internal\git\cmd'))

[Code]
function GetDefaultDir(Param: String): String;
begin
  if DirExists('D:\') then
    Result := 'D:\RepoPulse'
  else
    Result := ExpandConstant('{localappdata}\Programs\RepoPulse');
end;

{ 用户 PATH 里有没有目标目录。Windows 的 PATH 不区分大小写，所以两边都转大写比。 }
function PathHasGitEntry(Target: String): Boolean;
var
  Value: String;
begin
  Result := False;
  if not RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Value) then
    Exit;
  Result := Pos(';' + Uppercase(Target) + ';', ';' + Uppercase(Value) + ';') > 0;
end;

{ 只在目录真的存在、且还没登记过时才写，免得写进一个用不上的路径。 }
function NeedsGitPath(Target: String): Boolean;
begin
  Result := DirExists(Target) and (not PathHasGitEntry(Target));
end;

{ 从 PATH 里删掉目标目录，其他项保持原顺序（卸载时用）。 }
function RemoveGitPathEntry(Value: String; Target: String): String;
var
  Rest: String;
  Part: String;
  Kept: String;
  P: Integer;
begin
  Kept := '';
  Rest := Value;
  while Rest <> '' do
  begin
    P := Pos(';', Rest);
    if P > 0 then
    begin
      Part := Copy(Rest, 1, P - 1);
      Rest := Copy(Rest, P + 1, Length(Rest));
    end
    else
    begin
      Part := Rest;
      Rest := '';
    end;
    if (Trim(Part) <> '') and (Uppercase(Trim(Part)) <> Uppercase(Trim(Target))) then
    begin
      if Kept <> '' then
        Kept := Kept + ';';
      Kept := Kept + Trim(Part);
    end;
  end;
  Result := Kept;
end;

{ 卸载时把自己加的那段 PATH 撤掉 —— 不留垃圾，也不动用户原有的项。 }
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Value: String;
  Target: String;
begin
  if CurUninstallStep = usUninstall then
  begin
    Target := ExpandConstant('{app}\_internal\git\cmd');
    if PathHasGitEntry(Target) then
      if RegQueryStringValue(HKEY_CURRENT_USER, 'Environment', 'Path', Value) then
        RegWriteExpandStringValue(
          HKEY_CURRENT_USER, 'Environment', 'Path', RemoveGitPathEntry(Value, Target)
        );
  end;
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
