; IRMS-Scale 설치 파일 (Inno Setup 6)
;
; 빌드 절차 (scale_agent 디렉터리에서):
;   1) py -3.13 -m PyInstaller irms_scale.spec --clean --noconfirm
;   2) "C:\Program Files (x86)\Inno Setup 6\ISCC.exe" irms_scale_setup.iss
;   → 산출물: dist\IRMS-Scale-Setup.exe
;
; 설계:
; - 관리자 권한 불필요(per-user): %LOCALAPPDATA%\IRMS-Scale 에 설치.
;   자동 실행이 HKCU Run 키(에이전트와 동일 키)라 per-user 가 자연스럽다.
; - 설치/제거 전에 실행 중인 에이전트를 taskkill — 트레이 앱이 파일을 잡고
;   있으면 교체가 실패하기 때문.
; - Run 키는 설치 시 새 경로로 즉시 갱신(agent 도 기동 시 자가 치유하지만,
;   설치 후 바로 재부팅하는 경우까지 방어). 제거 시 함께 삭제.

#define AppName "IRMS-Scale"
#define AppVersion "2026.08.02"
#define AppExe "IRMS-Scale.exe"

[Setup]
AppId={{7E31C2A4-30A5-4B7B-9E5D-IRMSSCALE001}
AppName={#AppName}
AppVersion={#AppVersion}
AppPublisher=INTEROJO
DefaultDirName={localappdata}\{#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=dist
OutputBaseFilename=IRMS-Scale-Setup
Compression=lzma2
SolidCompression=yes
CloseApplications=yes
RestartApplications=no
UninstallDisplayName={#AppName} (저울 연동 에이전트)

[Languages]
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Tasks]
Name: "autostart"; Description: "Windows 시작 시 자동 실행 (권장)"; Flags: checkedonce

[Files]
Source: "dist\IRMS-Scale\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userprograms}\{#AppName}"; Filename: "{app}\{#AppExe}"

[Registry]
; 에이전트와 같은 키(HKCU\...\Run, 값 이름 IRMS-Scale)를 새 설치 경로로 갱신.
Root: HKCU; Subkey: "Software\Microsoft\Windows\CurrentVersion\Run"; \
    ValueType: string; ValueName: "{#AppName}"; ValueData: """{app}\{#AppExe}"""; \
    Flags: uninsdeletevalue; Tasks: autostart

[Run]
Filename: "{app}\{#AppExe}"; Description: "설치 후 바로 실행"; \
    Flags: postinstall nowait skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/C taskkill /IM {#AppExe} /F"; \
    Flags: runhidden; RunOnceId: "KillAgent"

[Code]
{ 설치 직전에 실행 중인 에이전트를 내린다 — 파일 교체 실패 방지. }
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  ResultCode: Integer;
begin
  Exec(ExpandConstant('{cmd}'), '/C taskkill /IM {#AppExe} /F', '',
       SW_HIDE, ewWaitUntilTerminated, ResultCode);
  { taskkill 이 프로세스 없음(128)이어도 무시 — 안 떠 있으면 그만. }
  Sleep(500);
  Result := '';
end;
