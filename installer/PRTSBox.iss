; Build with scripts/build_installer.py. SourceRoot, ReleaseVersion and OutputRoot
; are supplied by that script after it validates and extracts the portable zip.

[Setup]
AppId={{237da98b-a1ec-4e05-b629-f302e6efda87}
AppName=PRTSBox
AppVersion={#ReleaseVersion}
AppVerName=PRTSBox v{#ReleaseVersion}
VersionInfoVersion={#ReleaseVersion}.0.0
VersionInfoTextVersion=v{#ReleaseVersion}
VersionInfoProductTextVersion=v{#ReleaseVersion}
AppPublisher=FrostLeafKEE
AppPublisherURL=https://github.com/FrostLeafKEE/PRTSBox
AppSupportURL=https://github.com/FrostLeafKEE/PRTSBox/issues
AppUpdatesURL=https://github.com/FrostLeafKEE/PRTSBox/releases
DefaultDirName={localappdata}\Programs\PRTSBox
DefaultGroupName=PRTSBox
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
SetupArchitecture=x64
MinVersion=10.0
WizardStyle=modern
ShowLanguageDialog=yes
UsePreviousAppDir=yes
UsePreviousTasks=yes
CloseApplications=yes
RestartApplications=no
Uninstallable=yes
UninstallDisplayIcon={app}\PRTSBox.exe
SetupIconFile=..\prtsbox\ui\assets\app.ico
OutputDir={#OutputRoot}
OutputBaseFilename=PRTSBox-Setup-v{#ReleaseVersion}-win64
Compression=lzma2/normal
SolidCompression=yes

[Languages]
Name: "en"; MessagesFile: "compiler:Default.isl"
Name: "zh"; MessagesFile: "compiler:Languages\ChineseSimplified.isl"

[CustomMessages]
en.Shortcuts=Shortcuts:
zh.Shortcuts=快捷方式：
en.DesktopShortcut=Create a desktop shortcut
zh.DesktopShortcut=创建桌面快捷方式
en.StartMenuShortcut=Create Start Menu shortcuts
zh.StartMenuShortcut=创建开始菜单快捷方式
en.UninstallShortcut=Uninstall PRTSBox
zh.UninstallShortcut=卸载 PRTSBox
en.LaunchProgram=Launch PRTSBox
zh.LaunchProgram=启动 PRTSBox
en.RemoveDataPrompt=Also delete PRTSBox settings, logs, downloaded models and runtime? Choose No to keep them for a future installation.
zh.RemoveDataPrompt=是否同时删除 PRTSBox 的设置、日志、已下载模型和运行时？选择“否”可保留这些数据以供日后重新安装。

[Tasks]
Name: "desktopicon"; Description: "{cm:DesktopShortcut}"; GroupDescription: "{cm:Shortcuts}"; Flags: unchecked
Name: "startmenuicon"; Description: "{cm:StartMenuShortcut}"; GroupDescription: "{cm:Shortcuts}"

[Files]
Source: "{#SourceRoot}\PRTSBox.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\_internal\*"; DestDir: "{app}\_internal"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "{#SourceRoot}\使用说明.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\README.zh-CN.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\LICENSE"; DestDir: "{app}"; Flags: ignoreversion
Source: "{#SourceRoot}\COPYING.GPL"; DestDir: "{app}"; Flags: ignoreversion

[Icons]
Name: "{autodesktop}\PRTSBox"; Filename: "{app}\PRTSBox.exe"; Tasks: desktopicon
Name: "{autoprograms}\PRTSBox\PRTSBox"; Filename: "{app}\PRTSBox.exe"; Tasks: startmenuicon
Name: "{autoprograms}\PRTSBox\{cm:UninstallShortcut}"; Filename: "{uninstallexe}"; Tasks: startmenuicon

[Run]
Filename: "{app}\PRTSBox.exe"; Description: "{cm:LaunchProgram}"; Flags: nowait postinstall skipifsilent

[Code]
var
  RemoveData: Boolean;

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
begin
  if (CurUninstallStep = usUninstall) and (not UninstallSilent) then
    RemoveData := MsgBox(ExpandConstant('{cm:RemoveDataPrompt}'),
      mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;

  if (CurUninstallStep = usPostUninstall) and RemoveData then
    DelTree(ExpandConstant('{app}\data'), True, True, True);
end;
