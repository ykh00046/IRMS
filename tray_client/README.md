# IRMS Notice Tray Client

IRMS 공지방(`notice`) 메시지를 현장 PC에서 자동으로 음성(TTS)으로 재생하고,
당일 **근태 이상(출퇴근 누락, 지각, 조퇴)**이 있으면 1시간마다 조용한
Windows 토스트로 이름을 알려주는 Windows 작업표시줄 상주 프로그램입니다.

- 대상 OS: Windows 10 / 11 (64bit)
- 서버: 내부망 IRMS 호스팅 서버 (기본값 `http://192.168.11.147:9000`)
- 배포 규모: 현장 PC 7대 (확장 가능)
- 방향성: **수신 전용** (쓰기 불가, 로그인 없음, 내부망만 허용)
- 현재 버전: **1.1.0** (근태 이상 알림 추가)

---

## 1. 최종 사용자 가이드 (현장 PC)

### 설치

1. 관리자가 배포한 `IRMS-Notice-Setup-1.1.0.exe` 더블클릭
2. Windows가 "알 수 없는 게시자" 경고를 띄우면 **"추가 정보" → "실행"**
3. 한국어 설치 마법사에서 **다음 → 다음 → 설치**
4. "Windows 시작 시 자동 실행" 체크(기본)
5. 설치 완료 → 작업표시줄 오른쪽 아래 **IRMS 아이콘**이 뜨면 정상

### 사용

| 트레이 우클릭 메뉴 | 설명 |
|------------------|------|
| 상태: 연결됨 / 대기 중 / 오프라인 | 현재 서버 연결 상태(클릭 불가, 정보용) |
| 음소거 / 음소거 해제 | 이 PC만 공지 TTS를 끄기 (다른 PC에는 영향 없음) |
| 오늘 근태 알림 끄기 / 켜기 | 오늘 하루만 근태 이상 팝업을 안 보이게. **자정에 자동 복귀** |
| 테스트 재생 | "테스트 공지입니다" 샘플 음성 재생 |
| 근태 알림 테스트 | 현재 이상자가 있는 경우 바로 토스트 한 번 발동 |
| 로그 폴더 열기 | 문제 발생 시 로그 파일 위치로 이동 |
| 종료 | 프로그램 완전 종료 (PC 재부팅 시 다시 실행됨) |

### 공지를 받으면

관리자가 IRMS 웹의 **전체 공지방**에 메시지를 올리면, 약 5초 이내에 전 PC에서 **"띵동" → "관리자님: 내용"** 순으로 자동 재생됩니다.

### 근태 이상 알림 (v1.1.0+)

오늘 평일 근무자 중 **출근·퇴근 누락, 지각, 조퇴**가 하나라도 있으면
1시간마다 조용한 Windows 토스트(소리 없음)가 뜹니다.

- 제목: `근태 이상 N건`
- 본문: `김민호 · 전명옥` (3명 초과 시 `외 N명`)
- 해당자가 뒤늦게 출근 기록을 남기면 **다음 주기부터 자동으로 빠짐**
- 지각·조퇴는 당일 자정까지 반복 → **다음 날 자동 소거**
- 귀찮으면 트레이 우클릭 → **"오늘 근태 알림 끄기"** (자정에 자동 복귀)

### 자주 묻는 질문

- **소리가 안 나요** → 우클릭 → 음소거 해제, PC 볼륨 확인, "테스트 재생" 메뉴 실행
- **"오프라인"으로 표시돼요** → 서버 PC가 꺼져있거나 네트워크 장애. 자동으로 재접속을 시도합니다.
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
- `tray_client\Output\IRMS-Notice-Setup-1.0.0.exe` — **배포용 설치 파일** (약 30MB)

### 2.2 현장 PC 7대 설치

1. `IRMS-Notice-Setup-1.0.0.exe` 를 USB / 공유 폴더로 복사
2. 각 PC에서 관리자 권한으로 실행 → 다음 → 다음 → 설치
3. 설치 직후 자동 실행됨. 트레이 아이콘 확인
4. 우클릭 → "테스트 재생"으로 스피커 동작 확인
5. 관리자가 IRMS 공지방에 "설치 확인용 공지" 전송 → 전 PC에서 수신되는지 확인

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
│   ├── main.py          # pystray 기반 트레이 앱 진입점
│   ├── poller.py        # /api/public/notice/poll 백그라운드 폴링
│   ├── tts.py           # pyttsx3 + winsound (큐잉)
│   ├── config.py        # %APPDATA% JSON 설정 파일
│   ├── logger.py        # 일별 로테이팅 파일 로거
│   └── assets_gen.py    # 아이콘/wav 런타임 생성
├── assets/              # 빌드 시 자동 생성 (리포에 커밋 금지)
├── build/
│   ├── irms_notice.spec # PyInstaller 스펙
│   ├── installer.iss    # Inno Setup 스크립트
│   └── build.bat        # 원클릭 빌드
└── requirements.txt
```

### 3.2 로컬 실행 (개발 시)

```bat
cd tray_client
python src\assets_gen.py
python -m src.main
```

서버가 로컬에 있으면 `%APPDATA%\IRMS-Notice\config.json` 의 `server_url` 을 `http://127.0.0.1:9000` 으로 변경.

### 3.3 서버 API (이 클라이언트가 의존)

| Endpoint | 설명 |
|----------|------|
| `GET /api/public/notice/ping` | 헬스체크 |
| `GET /api/public/notice/poll?after_id=X&limit=20` | `after_id` 이후 새 공지 메시지 |

내부망 IP만 허용 (`127.0.0.1`, `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`). 외부 IP는 403.

### 3.4 설정 파일 스키마

`%APPDATA%\IRMS-Notice\config.json`:

```json
{
  "server_url": "http://192.168.11.147:9000",
  "poll_interval_seconds": 5,
  "muted": false,
  "last_message_id": 0,
  "tts_rate": 180,
  "volume": 1.0
}
```

- `last_message_id`: 최초 설치 시 0, 첫 폴링 때 서버의 현재 `latest_id`로 스냅샷(과거 공지 재생 방지)
- `poll_interval_seconds`: 최소 2초, 기본 5초
- 네트워크 장애 시 자동 지수 백오프 (5초 → 10 → 20 → 40 → 60초 상한)

### 3.5 로그

`%APPDATA%\IRMS-Notice\logs\tray.log` (자정 기준 로테이션, 7일 보관)

```
2026-04-23 14:00:01 | INFO  | starting IRMS Notice tray (server=http://192.168.11.147:9000, poll=5s)
2026-04-23 14:00:03 | INFO  | initial sync: snapshot latest_id=42
2026-04-23 14:05:12 | INFO  | notice received id=43 오늘 17시 설비 점검 예정입니다.
```

### 3.6 알려진 제약

- Windows 전용 (pyttsx3 SAPI5, winsound, pywin32 의존)
- 코드 서명 없음 → 설치 시 SmartScreen 경고 발생 가능
- 단일 인스턴스 강제 기능 없음 (Windows 시작프로그램 등록으로 사실상 1회만 실행됨)
- v1에서는 자동 업데이트 없음 — 새 버전 배포 시 수동 재설치

---

## 4. 문서 링크

- 계획서: [`docs/01-plan/features/notice-tray-client.plan.md`](../docs/01-plan/features/notice-tray-client.plan.md)
- 설계서: [`docs/02-design/features/notice-tray-client.design.md`](../docs/02-design/features/notice-tray-client.design.md)
