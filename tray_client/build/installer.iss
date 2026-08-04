; Inno Setup script for IRMS Notice tray client.
;
; Build with Inno Setup 6+ after PyInstaller:
;   iscc build\installer.iss
;
; Output: Output\IRMS-Notice-Setup-3.1.10.exe
;
; MyAppVersion 은 tray_client/src/version.py 의 __version__ 과 반드시 같아야 한다
; (tests/test_tray_installer_script.py 가 동기화를 검사한다).

#define MyAppName "IRMS 현장 도우미"
#define MyAppVersion "3.1.10"
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

[InstallDelete]
; PyInstaller onedir 업그레이드에서 Inno 는 '사라진 파일'을 지우지 않는다 — 다른 python
; 으로 빌드된 구버전의 _internal 잔재(pyd·네임스페이스 디렉터리)가 새 exe 에 phantom
; module 로 import 돼 기동 즉사한 실사고 2건(brotlicffi/backports.zstd AttributeError,
; 2026-08-03). 설치 전에 앱 폴더를 통째로 비운다 — 사용자 상태는 %APPDATA% 라 안전.
Type: filesandordirs; Name: "{app}"

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
