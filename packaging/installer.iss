; Sookit Inno Setup 安装脚本
; 用法: ISCC.exe packaging/installer.iss
; 安装目录: 默认 Program Files\Sookit；父路径可自选，末级目录强制为 Sookit（见 [Code] 自动补全）；运行时数据在 %APPDATA%/%LOCALAPPDATA%；yt-dlp 自动下载到 %LOCALAPPDATA%

#define MyAppName "Sookit"
#define MyAppVersion "261008.1"
#define MyAppPublisher "sodaa-l"
#define MyAppExeName "Sookit.exe"
#define MyAppId "{{F3A8B7C2-5E4D-4A2B-9C1E-8B7D6A5F4E3D}"
; 卸载注册表键名 = AppId 去掉一层大括号 + _is1，供 [Code] 的升装判定使用（键名已实测确认）
#define MyAppRegKey "{F3A8B7C2-5E4D-4A2B-9C1E-8B7D6A5F4E3D}_is1"

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

; 安装类型（向导「选择组件」页顶部的单选项）。自定义类型必须带 iscustom：
; 官方文档明确「如果没有定义自定义类型，Setup 将只允许用户选择预设安装类型，
; 用户将不能再手动选择/取消选择组件」—— 组件能否被手动勾选完全取决于这个标志。
[Types]
Name: "full"; Description: "完整安装"
Name: "compact"; Description: "精简安装（不含 FFmpeg 及 yt-dlp + Deno）"
Name: "custom"; Description: "自定义安装（自行选择组件）"; Flags: iscustom

[Components]
; Types: full —— 仅「完整安装」默认勾选；精简与自定义下默认都不勾，由用户自行决定。
; 未加 fixed，因此始终可手动勾选/取消；一旦改动，安装类型会自动切到「自定义」。
Name: "ffmpeg"; Description: "FFmpeg（音视频组件）"; Types: full
Name: "ytdlp"; Description: "yt-dlp + Deno（视频下载核心）"; Types: full

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"
Name: "startmenuicon"; Description: "添加到开始菜单"; GroupDescription: "{cm:AdditionalIcons}"

[Files]
; 主条目带 ignoreversion（无条件覆盖），因此必须把两个可选组件的目录锚定排除在外，
; 交给下面各自绑组件的条目处理。Excludes 模式前导反斜杠 = 锚定到源树根
; （不加前导反斜杠会匹配任意层级的同名子路径，不够精确）。
Source: "..\dist\Sookit\*"; DestDir: "{app}"; Excludes: "\tools\yt-dlp\*,\tools\ffmpeg\*"; Flags: ignoreversion recursesubdirs createallsubdirs

; ---- 两个可选组件 ----
; 各用一条通配符条目 + 一个「升装闸门」Check（函数见 [Code] 的 PatchIfPresent）：
;   全新安装 → Check 恒为 True，按组件选择正常安装；
;   升级安装 → 仅当该目录的**主文件**已存在才允许安装该条目 —— 用户上次没装过的不会被新增，
;              已装的再由 Inno 默认替换规则按 PE 版本信息决定是否覆盖。
; 闸门必须判「文件」而不是「目录」：主条目的 createallsubdirs 会把 tools\ffmpeg、tools\yt-dlp
; 两个空目录也建出来（Excludes 只排文件、不排目录，已实测），用 DirExists 会被空目录骗过去、
; 导致升装时把 100+MB 的组件重新塞给没装过的用户。
; 两条都刻意不用 ignoreversion：交给 Inno 默认规则按版本信息比较（已存在文件更新才覆盖、
; 相同或更新则保留），这样不会把用户已通过设置页更新过的 yt-dlp/deno 打回安装包内的旧版。
; 前提是这些 exe 带 VS 版本资源，已实测：yt-dlp 2026.08.19 → 2026.8.19.0、deno 2.9.7 → 2.9.7.0。
; 注意：源目录必须存在，否则 ISCC 编译失败（CI 由 release.yml 先下载，本地打包需自备这两个目录）。
Source: "..\dist\Sookit\tools\ffmpeg\*"; DestDir: "{app}\tools\ffmpeg"; Components: ffmpeg; Check: PatchIfPresent('tools\ffmpeg\ffmpeg.exe')
Source: "..\dist\Sookit\tools\yt-dlp\*"; DestDir: "{app}\tools\yt-dlp"; Components: ytdlp; Check: PatchIfPresent('tools\yt-dlp\yt-dlp.exe')

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
// ---- 升装判定与「补丁式安装」闸门 ----
// 注意：[Code] 段是 Pascal 源码，注释只能用 // 或 { }，不能用 ;（; 是语句结束符，
// 写成 ; 注释会在编译时报 'BEGIN' expected —— 踩过一次）。
// 升装与全新安装必须区分开：闸门里若不分青红皂白地要求"文件已存在"，全新安装就会什么都不装。
// 判定信号 = 卸载注册表键（上次安装写过、本次安装还没写）。
// 不用「{app}\Sookit.exe 是否已存在」来判：Inno 按声明顺序装 [Files]，主条目先写 Sookit.exe，
// 等轮到组件条目时它已存在，全新安装也会被误判成升装（已实测踩过）。
var
  gUpgradeProbe: Integer;  // -1=未判定, 0=全新安装, 1=升级安装

function IsUpgradeInstall(): Boolean;
var
  Key, Probe: String;
begin
  if gUpgradeProbe < 0 then
  begin
    Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#MyAppRegKey}';
    Probe := '';
    // 四种存放位置全查，不依赖"HKLM 默认是哪个视图"这种细节：
    //   HKLM（进程默认视图）、HKLM32（显式 32 位视图）、HKLM64（显式 64 位视图）、
    //   HKCU（非管理员安装时落这里）。任一处命中即判为升装。
    if RegQueryStringValue(HKLM, Key, 'UninstallString', Probe) then
      gUpgradeProbe := 1
    else if RegQueryStringValue(HKLM32, Key, 'UninstallString', Probe) then
      gUpgradeProbe := 1
    else if RegQueryStringValue(HKLM64, Key, 'UninstallString', Probe) then
      gUpgradeProbe := 1
    else if RegQueryStringValue(HKCU, Key, 'UninstallString', Probe) then
      gUpgradeProbe := 1
    else
      gUpgradeProbe := 0;
  end;
  Result := gUpgradeProbe = 1;
end;

// 升级安装时跳过「选择组件」页：用户不再面对 完整/精简/自定义 三选一，改由下面两条闸门自动决定。
function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := (PageID = wpSelectComponents) and IsUpgradeInstall();
end;

// [Files] 两条可选组件的升装闸门：全新安装恒放行；升级安装仅当该文件已存在才放行。
// 于是"上次没装"的组件不会被新增回来；已装的仍由 Inno 默认版本规则决定要不要覆盖。
function PatchIfPresent(const RelPath: String): Boolean;
begin
  Result := (not IsUpgradeInstall()) or FileExists(ExpandConstant('{app}\') + RelPath);
end;

function InitializeSetup(): Boolean;
begin
  gUpgradeProbe := -1;
  Result := True;
end;

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
