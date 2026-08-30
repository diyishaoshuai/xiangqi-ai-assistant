#define MyAppName "象棋 AI 助手"
#define MyAppVersion "2026.08.31"
#define MyAppPublisher "diyishaoshuai"
#define MyAppExeName "XiangqiAI.exe"

[Setup]
AppId={{E81D6970-29D6-4A26-9B62-8F0AA83A1260}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\XiangqiAI
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=..\dist\installer
OutputBaseFilename=XiangqiAI-Setup-{#MyAppVersion}
#ifdef QuickCompile
Compression=none
#else
Compression=lzma2/ultra64
#endif
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
MinVersion=10.0
UninstallDisplayIcon={app}\{#MyAppExeName}
InfoBeforeFile=..\THIRD_PARTY_NOTICES.md
VersionInfoVersion=2026.8.31.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} 安装程序
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion={#MyAppVersion}
RestartApplications=no

[Tasks]
Name: "desktopicon"; Description: "创建桌面快捷方式"; GroupDescription: "附加快捷方式："; Flags: unchecked

[Files]
Source: "..\dist\XiangqiAI\*"; DestDir: "{app}"; Excludes: "logs\*,__pycache__\*,*.pyc,*.pyo,*.log"; Flags: ignoreversion recursesubdirs; AfterInstall: RecordInstalledFile
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: RecordInstalledFile
Source: "..\THIRD_PARTY_NOTICES.md"; DestDir: "{app}"; Flags: ignoreversion; AfterInstall: RecordInstalledFile

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\卸载 {#MyAppName}"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "启动 {#MyAppName}"; Flags: nowait postinstall skipifsilent

[UninstallDelete]
Type: files; Name: "{app}\.xiangqi-installed-files.txt"

[Code]
#include "uninstall_support.iss"
