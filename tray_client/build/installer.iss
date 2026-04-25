; Inno Setup script for IRMS Notice tray client.
;
; Build with Inno Setup 6+ after PyInstaller:
;   iscc build\installer.iss
;
; Output: Output\IRMS-Notice-Setup-1.1.5.exe

#define MyAppName "IRMS Notice"
#define MyAppVersion "1.1.5"
#define MyAppPublisher "IRMS"
#define MyAppExeName "IRMS-Notice.exe"

[Setup]
AppId={{5E4E7CF9-1A8C-4E50-9B24-9C90C9B5F001}}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\IRMS-Notice
DefaultGroupName=IRMS Notice
DisableProgramGroupPage=yes
OutputBaseFilename=IRMS-Notice-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
PrivilegesRequired=lowest
MinVersion=10.0
UninstallDisplayName={#MyAppName}
UninstallDisplayIcon={app}\{#MyAppExeName}
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "desktopicon"; Description: "바탕화면 바로가기 만들기"; GroupDescription: "추가 아이콘:"; Flags: unchecked
Name: "startupicon"; Description: "Windows 시작 시 자동 실행 (권장)"; GroupDescription: "시작 옵션:"

[Files]
Source: "..\dist\IRMS-Notice\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{group}\{#MyAppName} 제거"; Filename: "{uninstallexe}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Registry]
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "IRMS-Notice"; \
    ValueData: """{app}\{#MyAppExeName}"""; \
    Flags: uninsdeletevalue; Tasks: startupicon

[Run]
Filename: "{app}\{#MyAppExeName}"; Description: "설치 후 바로 실행"; Flags: nowait postinstall skipifsilent runasoriginaluser

[UninstallRun]
Filename: "{sys}\taskkill.exe"; Parameters: "/IM {#MyAppExeName} /F /T"; Flags: runhidden skipifdoesntexist; RunOnceId: "KillIRMSNoticeOnUninstall"

[Code]
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Log('Attempting to stop running IRMS Notice instances before install.');
  if Exec(
    ExpandConstant('{sys}\taskkill.exe'),
    '/IM {#MyAppExeName} /F /T',
    '',
    SW_HIDE,
    ewWaitUntilTerminated,
    ResultCode
  ) then
    Log(Format('taskkill finished with code %d', [ResultCode]))
  else
    Log(Format('taskkill launch failed with system error %d', [ResultCode]));

  Sleep(500);
  Result := '';
end;
