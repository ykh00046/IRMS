# IRMS Attendance Tray Client

해당 월에 **미처리 근태 이상(출퇴근 누락, 지각, 조퇴)**이 남아 있으면
정해진 시각(09:00, 13:00, 16:00)에 조용한 팝업으로 알려주는 Windows
작업표시줄 상주 프로그램입니다.

- 대상 OS: Windows 10 / 11 (64bit)
- 서버: 내부망 IRMS 호스팅 서버 (기본값 `http://192.168.11.147:9000`)
- 배포 규모: 현장 PC 7대 (확장 가능)
- 방향성: **수신 전용** (쓰기 불가, 로그인 없음, 내부망만 허용)
- 현재 버전: **2.0.0** (공지 TTS 브로드캐스트 제거, 근태 알림만 수행)

> 단체 공지 TTS 기능은 PyInstaller + SAPI 환경 편차로 안정적인 동작을 보장하기
> 어려워 2.0.0에서 완전히 제거했습니다. 관리자가 IRMS 웹의 공지방에 글을
> 남기는 기능 자체는 그대로 살아있어 기록·열람 용도로 사용 가능합니다.

---

## 1. 최종 사용자 가이드 (현장 PC)

### 설치

1. 관리자가 배포한 `IRMS-Notice-Setup-2.0.0.exe` 더블클릭
2. Windows가 "알 수 없는 게시자" 경고를 띄우면 **"추가 정보" → "실행"**
3. 한국어 설치 마법사에서 **다음 → 다음 → 설치**
4. "Windows 시작 시 자동 실행" 체크(기본)
5. 설치 완료 → 작업표시줄 오른쪽 아래 **IRMS 아이콘**이 뜨면 정상

### 사용

| 트레이 우클릭 메뉴 | 설명 |
|------------------|------|
| 근태 알림 오늘만 끄기 / 켜기 | 오늘 하루만 근태 이상 팝업을 안 보이게. **자정에 자동 복귀** |
| 근태 알림 테스트 | 테스트용 근태 팝업 1회 표시 |
| 근태 확인 열기 | 기본 브라우저로 `/attendance` 페이지 열기 |
| 로그 폴더 열기 | 문제 발생 시 로그 파일 위치로 이동 |
| 종료 | 프로그램 완전 종료 (PC 재부팅 시 다시 실행됨) |

### 근태 이상 알림

현재 월에 **미처리 출근·퇴근 누락, 지각, 조퇴**가 하나라도 남아 있으면
매일 **09:00, 13:00, 16:00**에 조용한 근태 팝업이 뜹니다.

- 제목: `근태 확인 필요`
- 본문: `김민호 · 전명옥` (3명 초과 시 `외 N명`)
- 해당 직원이 출근 기록을 보충하거나 ERP에서 처리되면 **다음 슬롯부터 자동으로 빠짐**
- 같은 이상이 남아 있으면 다음 슬롯(09:00 / 13:00 / 16:00)에도 다시 표시
- 트레이를 도중에 재시작해도 이미 떴던 슬롯은 다시 띄우지 않음 (30분 그레이스)
- 귀찮으면 트레이 우클릭 → **"근태 알림 오늘만 끄기"** (자정에 자동 복귀)

### 자주 묻는 질문

- **팝업이 안 떠요** → "근태 알림 테스트"로 동작 확인. 정상이면 이상자 자체가 없는 것.
- **아이콘이 안 보여요** → 작업표시줄 오른쪽의 ▲(숨겨진 아이콘) 클릭, 없으면 시작 메뉴에서 "IRMS Notice" 실행

### 제거

제어판 → 프로그램 추가/제거 → **IRMS Notice** 선택 → 제거

---

## 2. 관리자: 배포 절차

### 2.1 빌드 (1회)

**필수 소프트웨어** (빌드 PC 1대에만):
- Python 3.10 이상
- [Inno Setup 6](https://jrsoftware.org/isinfo.php) (설치 파일 생성용, 무료)

```bat
cd tray_client
pip install -r requirements.txt
pip install pyinstaller
build\build.bat
```

산출물:
- `tray_client\dist\IRMS-Notice\` — 실행 파일 폴더 (수동 복사용)
- `tray_client\build\Output\IRMS-Notice-Setup-2.0.0.exe` — **배포용 설치 파일**

### 2.2 현장 PC 7대 설치

1. `IRMS-Notice-Setup-2.0.0.exe`를 USB / 공유 폴더로 복사
2. 각 PC에서 더블클릭 → 다음 → 다음 → 설치 (이전 버전 위에 덮어쓰기)
3. 설치 직후 자동 실행됨. 트레이 아이콘 확인
4. 우클릭 → "근태 알림 테스트"로 팝업 동작 확인

### 2.3 서버 주소 변경

서버 IP가 바뀌면 각 PC에서:

1. 트레이 우클릭 → 로그 폴더 열기 (`%APPDATA%\IRMS-Notice\`)
2. `config.json` 메모장으로 열어 `"server_url"` 값 수정
3. 트레이 우클릭 → 종료 → 시작 메뉴에서 다시 실행

---

## 3. 개발자 가이드

### 3.1 프로젝트 구조

```
tray_client/
├── src/
│   ├── main.py             # pystray 기반 트레이 앱 진입점
│   ├── attendance_alerts.py # /api/public/attendance-alerts/month 폴러 (슬롯 스케줄)
│   ├── attendance_popup.py # 근태 이상 팝업 UI (Tkinter)
│   ├── config.py           # %APPDATA% JSON 설정 (server_url만 보존)
│   ├── logger.py           # 일별 로테이팅 파일 로거
│   └── assets_gen.py       # 아이콘/wav 런타임 생성
├── assets/                 # 빌드 시 자동 생성
├── build/
│   ├── irms_notice.spec    # PyInstaller 스펙
│   ├── installer.iss       # Inno Setup 스크립트
│   └── build.bat           # 원클릭 빌드
└── requirements.txt
```

### 3.2 로컬 실행 (개발 시)

```bat
cd tray_client
python src\assets_gen.py
python -m src.main
```

서버가 로컬에 있으면 `%APPDATA%\IRMS-Notice\config.json`의 `server_url`을 `http://127.0.0.1:9000`으로 변경.

### 3.3 서버 API (이 클라이언트가 의존)

| Endpoint | 설명 |
|----------|------|
| `GET /api/public/attendance-alerts/month` | 이번 달 미처리 근태 이상 목록 |

내부망 IP만 허용 (`127.0.0.1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). 외부 IP는 403.

### 3.4 설정 파일 스키마

`%APPDATA%\IRMS-Notice\config.json`:

```json
{
  "server_url": "http://192.168.11.147:9000"
}
```

이전 버전(1.x)에서 사용하던 `muted`, `tts_rate`, `volume`, `last_message_id`, `poll_interval_seconds` 필드는 자동으로 무시됩니다 (config.json은 그대로 두면 됨).

### 3.5 로그

`%APPDATA%\IRMS-Notice\logs\tray.log` (자정 기준 로테이션, 7일 보관)

```
2026-04-30 09:00:00 | INFO  | starting IRMS attendance tray (server=http://192.168.11.147:9000)
2026-04-30 09:00:01 | INFO  | attendance popup raised: 근태 확인 필요 / 이번 달 확인이 필요한 인원이 있습니다.
```

### 3.6 알려진 제약

- Windows 전용 (pystray, pywin32 의존)
- 코드 서명 없음 → 설치 시 SmartScreen 경고 발생 가능
- 자동 업데이트 없음 — 새 버전 배포 시 수동 재설치 (덮어쓰기)

---

## 4. 문서 링크

- 계획서: [`docs/01-plan/features/notice-tray-client.plan.md`](../docs/01-plan/features/notice-tray-client.plan.md)
- 설계서: [`docs/02-design/features/notice-tray-client.design.md`](../docs/02-design/features/notice-tray-client.design.md)
