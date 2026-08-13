; Sookit Inno Setup 安装脚本
; 用法: ISCC.exe packaging/installer.iss
; 安装目录: Program Files\Sookit；运行时数据在 %APPDATA%/%LOCALAPPDATA%；yt-dlp 自动下载到 %LOCALAPPDATA%

#define MyAppName "Sookit"
#define MyAppVersion "260811.1"
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
; 运行时数据全部在 %APPDATA%/%LOCALAPPDATA%，程序目录只读，无写权限问题

; 仅简体中文界面；中文语言文件已入库为项目依赖：packaging\ChineseSimplified.isl
[Languages]
Name: "chinesesimplified"; MessagesFile: "ChineseSimplified.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\Sookit\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "{cm:LaunchProgram,{#StringChange(MyAppName, '&', '&&')}}"; Flags: nowait postinstall skipifsilent
