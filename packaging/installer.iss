; Sookit Inno Setup 安装脚本
; 用法: ISCC.exe packaging/installer.iss
; 安装目录: 默认 Program Files\Sookit；父路径可自选，末级目录强制为 Sookit（见 [Code] 自动补全）；运行时数据在 %APPDATA%/%LOCALAPPDATA%；yt-dlp 自动下载到 %LOCALAPPDATA%

#define MyAppName "Sookit"
#define MyAppVersion "260830.1"
#define MyAppPublisher "sodaa-l"
#define MyAppExeName "Sookit.exe"
#define MyAppId "{{F3A8B7C2-5E4D-4A2B-9C1E-8B7D6A5F4E3D}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
OutputDir=..\dist
OutputBaseFilename=Sookit-Setup-{#MyAppVersion}
SetupIconFile=sookit.ico
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=admin
; 单实例互斥体（与 __main__.py 的 Local\Sookit 一致）：安装/卸载时检测 Sookit 是否在运行，
; 若在运行则弹窗询问（不自动关闭），避免 tools/ 下文件被占用导致卸载删不掉
AppMutex=Local\Sookit
; 运行时数据全部在 %APPDATA%/%LOCALAPPDATA%，程序目录只读，无写权限问题

; 仅简体中文界面；中文语言文件已入库为项目依赖：packaging\ChineseSimplified.isl
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startmenuicon"; Description: "添加到开始菜单"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
Source: "..\dist\Sookit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: startmenuicon
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent

; 卸载时删除整个程序目录 {app}（含 Inno [Files] 记录之外的 updater 运行时产物，
; 如 tools\yt-dlp、tools\.ytdlp_updater_result*.json；卸载时 AppMutex 已阻止 Sookit 运行）
; 以及用户运行时数据（配置、日志、封面缓存）
; {userappdata} = %APPDATA%，{localappdata} = %LOCALAPPDATA%
[UninstallDelete]
Type: filesandordirs; Name: "{app}"
Type: filesandordirs; Name: "{userappdata}\{#MyAppName}"
Type: filesandordirs; Name: "{localappdata}\{#MyAppName}"

; 强制安装目录末级为 Sookit：父路径可自选，若用户所选目录的最后一级不是 Sookit，
; 点「下一步」时自动追加 \Sookit（如 D:\Apps → D:\Apps\Sookit，D:\ → D:\Sookit）。
; 已以 Sookit 结尾（不区分大小写）则原样保留，不会重复追加。
; 注意：静默安装（/VERYSILENT /DIR=...）不显示向导页，不走该回调，无法校验。
[Code]
function NextButtonClick(CurPageID: Integer): Boolean;
var
  Dir: string;
begin
  Result := True;
  if CurPageID = wpSelectDir then
  begin
    Dir := RemoveBackslashUnlessRoot(WizardDirValue());
    if CompareText(ExtractFileName(Dir), 'Sookit') <> 0 then
      WizardForm.DirEdit.Text := AddBackslash(Dir) + 'Sookit';
  end;
end;
