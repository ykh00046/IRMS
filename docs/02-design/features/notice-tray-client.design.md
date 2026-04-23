# Notice Tray Client Design

> 공지방 메시지를 현장 PC에서 자동 TTS 재생하는 트레이 클라이언트 상세 설계서

## 1. Overview

| Item | Detail |
|------|--------|
| Feature | notice-tray-client |
| Plan | `docs/01-plan/features/notice-tray-client.plan.md` |
| Scope | 서버 무인증 API + Windows 트레이 앱 + 설치 파일 |
| Target | Windows 10+ 7대, 내부망 전용 |

## 2. Architecture

```
┌─────────────────────────────────────────────────────────┐
│ Server (192.168.11.147:9000)                            │
│                                                          │
│   FastAPI  ─ public_router  ─ GET /api/public/notice/poll│
│                               GET /api/public/notice/ping│
│                                                          │
│   InternalNetworkOnlyMiddleware (신규)                   │
│   └─ 허용: 127.0.0.1, 192.168.0.0/16, 10.0.0.0/8        │
│   └─ 거부: 외부망 → 403                                  │
└─────────────────────────────────────────────────────────┘
              ▲
              │ HTTP GET (5초 간격, exponential backoff)
              │
┌─────────────┴───────────────────────────────────────────┐
│ Tray Client (Windows Service-like, user session)        │
│                                                          │
│   main.py ──► poller.py ──► tts.py ──► Windows SAPI     │
│       │         │              │                         │
│       │         │              └─► winsound (띵동 wav)   │
│       │         │                                        │
│       │         └─► state.json (%APPDATA%)              │
│       │                                                  │
│       └─► pystray (트레이 아이콘, 우클릭 메뉴)           │
└─────────────────────────────────────────────────────────┘
```

## 3. Server API Design

### 3.1 GET /api/public/notice/ping

헬스체크. 트레이 앱 시작 시 서버 연결 확인용.

**Request:** 없음
**Response:**
```json
{"status": "ok", "time": "2026-04-23T05:00:00Z"}
```
**Auth:** 없음 (내부망 IP 체크만)

### 3.2 GET /api/public/notice/poll

공지방 신규 메시지 조회. 트레이 앱이 5초마다 호출.

**Query Params:**
| Param | Type | Default | Description |
|-------|------|---------|-------------|
| after_id | int | 0 | 마지막으로 수신한 메시지 ID. 0이면 "현재까지 상태"만 반환 (초기 동기화) |
| limit | int | 20 | 한 번에 가져올 최대 메시지 수 |

**Response:**
```json
{
  "room": {"key": "notice", "name": "전체 공지방", "scope": "notice"},
  "items": [
    {
      "id": 102,
      "message_text": "오늘 17시 설비 점검 예정입니다.",
      "created_by_display_name": "관리자A",
      "created_by_username": "admin01",
      "created_at": "2026-04-23T05:00:12Z"
    }
  ],
  "latest_id": 102,
  "total": 1
}
```

**동작:**
- `after_id=0`이면 `items=[]`, `latest_id=현재 max(id)` 반환 (최초 동기화 시 과거 공지 폭탄 방지)
- `after_id>0`이면 `id > after_id` 메시지만 ASC 정렬로 반환

**Auth:** 없음
**Rate limit:** slowapi로 `30/minute` (7대 × 12req/분 = 84req/분 여유)
**구현 위치:** `src/routers/public_notice_routes.py` (신규)

### 3.3 Internal Network IP Whitelist

**미들웨어:** `src/middleware/internal_only.py` (신규)

**화이트리스트:**
- `127.0.0.1`, `::1` (localhost)
- `192.168.0.0/16` (사설망)
- `10.0.0.0/8` (사설망)
- `172.16.0.0/12` (사설망)

**동작:**
- `/api/public/notice/*` 경로에만 적용
- 외부 IP → 403 `{"detail": "INTERNAL_NETWORK_ONLY"}`
- `X-Forwarded-For` 헤더는 신뢰하지 않음 (reverse proxy 없음 가정)

**구현 위치:** `src/main.py` → `create_app()`에 추가

### 3.4 CSRF 예외 등록

`src/main.py`의 `CSRFMiddleware exempt_urls`에 추가:
```python
re.compile(r"^/api/public/notice/.*"),
```

## 4. Tray Client Design

### 4.1 프로젝트 구조

```
tray_client/
├── src/
│   ├── __init__.py
│   ├── main.py              # 진입점, pystray 루프
│   ├── poller.py            # 서버 polling + 백오프
│   ├── tts.py               # 음성 + 효과음 재생, 큐잉
│   ├── config.py            # 설정 로드/저장
│   └── assets_gen.py        # 아이콘 + 띵동 wav 런타임 생성
├── assets/
│   ├── icon.ico             # 트레이 아이콘 (빌드 시 생성)
│   └── ding.wav             # 효과음 (빌드 시 생성)
├── build/
│   ├── irms_notice.spec     # PyInstaller 스펙
│   └── installer.iss        # Inno Setup 스크립트
├── requirements.txt
└── README.md
```

### 4.2 런타임 설정 파일

**경로:** `%APPDATA%\IRMS-Notice\config.json`

```json
{
  "server_url": "http://192.168.11.147:9000",
  "poll_interval_seconds": 5,
  "muted": false,
  "last_message_id": 0,
  "tts_voice": "ko-KR",
  "tts_rate": 180,
  "volume": 1.0
}
```

**최초 실행 시:** 설치 시 함께 배포된 기본값 복사
**변경 시점:** 트레이 메뉴에서 음소거 토글 / 새 메시지 수신 시 `last_message_id` 갱신

### 4.3 메인 루프 (main.py)

```python
# 의사 코드
def main():
    config = load_or_init_config()
    tts_queue = TTSQueue()         # 백그라운드 재생 스레드
    poller = Poller(config, tts_queue)
    
    icon = create_tray_icon(
        menu=[
            MenuItem("상태: 대기 중", enabled=False),
            MenuItem("음소거 [] ", toggle_mute),
            MenuItem("테스트 재생", play_test),
            MenuItem("로그 열기", open_log_folder),
            MenuItem("종료", quit_app),
        ],
    )
    
    poller.start()  # 별도 스레드
    icon.run()      # 메인 스레드
```

### 4.4 Polling 로직 (poller.py)

**초기화:**
- 시작 시 `GET /ping` 호출 → 성공하면 `GET /poll?after_id=0`으로 현재 `latest_id` 획득
- `config.last_message_id`를 `latest_id`로 설정 → 과거 메시지 TTS 폭탄 방지

**정상 루프:**
```python
while not stop_event.is_set():
    try:
        resp = requests.get(f"{server_url}/api/public/notice/poll",
                            params={"after_id": config.last_message_id},
                            timeout=10)
        resp.raise_for_status()
        data = resp.json()
        
        for item in data["items"]:
            tts_queue.enqueue(item)  # TTS 스레드에서 순차 재생
            config.last_message_id = item["id"]
            save_config(config)
        
        current_backoff = BASE_INTERVAL  # 5초로 복귀
        stop_event.wait(current_backoff)
    except (requests.ConnectionError, requests.Timeout):
        log("connection error, backing off")
        stop_event.wait(current_backoff)
        current_backoff = min(current_backoff * 2, 60)  # 5→10→20→40→60
```

**정지:** `stop_event.set()` → 다음 반복에서 종료

### 4.5 TTS 재생 (tts.py)

**엔진:** `pyttsx3` (Windows SAPI5 래퍼, 오프라인)

**초기화:**
```python
engine = pyttsx3.init("sapi5")
# 한국어 음성 선택 (Windows 10 기본: Microsoft Heami)
for voice in engine.getProperty("voices"):
    if "ko" in voice.id.lower() or "Heami" in voice.name:
        engine.setProperty("voice", voice.id)
        break
engine.setProperty("rate", 180)
```

**재생 큐 (중요):**
- `pyttsx3` 는 thread-safe하지 않아 **단일 워커 스레드**에서 순차 처리
- 큐에 쌓인 순서대로 재생, 중간에 새 메시지가 와도 현재 재생은 안 끊김
- 음소거 상태이면 큐에서 꺼내고 **무시** (뒤늦게 해제해도 과거 메시지 안 울림)

**재생 포맷 (기존 `chat.js:201-205` 방식 유지):**
```python
speaker = msg["created_by_display_name"] or msg["created_by_username"] or ""
text = f"{speaker}님: {msg['message_text']}" if speaker else msg["message_text"]
```

**효과음:**
- TTS 시작 직전 `winsound.PlaySound(ding_wav_path, winsound.SND_FILENAME | winsound.SND_ASYNC)`
- 짧은 delay(200ms) 후 TTS 시작 → 벨소리가 끝난 뒤 음성 시작

### 4.6 Tray Menu 동작

| 메뉴 | 동작 |
|------|------|
| **상태: 대기 중 / 연결됨 / 오프라인** | 폴링 상태 표시 (비활성, 주기 갱신) |
| **음소거 ☐ / ☒** | 토글, `config.muted` 저장. ON이면 TTS 큐에서 메시지 무시 |
| **테스트 재생** | "테스트 공지입니다" 즉시 재생 (설치 직후 동작 확인용) |
| **로그 열기** | `%APPDATA%\IRMS-Notice\logs\` 탐색기로 열기 |
| **종료** | 폴링 중지 → 큐 플러시 → 앱 종료 |

### 4.7 로그

**경로:** `%APPDATA%\IRMS-Notice\logs\tray-YYYY-MM-DD.log`
**포맷:** `2026-04-23 14:00:01 | INFO  | polled, new_messages=1`
**레벨:** INFO / WARN / ERROR
**로테이션:** 일별(`TimedRotatingFileHandler`), 최근 7일 유지

### 4.8 에셋 자동 생성 (assets_gen.py)

**빌드 시점에** `build/build.py`가 호출하여 `assets/` 폴더에 파일 생성.

**icon.ico:**
```python
# Pillow로 16×16, 32×32, 48×48 다중 사이즈 ico 생성
# 배경: #1e40af (남색), 텍스트: "IRMS" 흰색 볼드
```

**ding.wav:**
```python
# wave + struct로 "띵동" 톤 합성 (440Hz → 660Hz, 각 200ms)
# 44100Hz, 16-bit mono
```

이 방식의 장점: **별도 에셋 파일 없이 Python만으로 완결** (리포지토리에 바이너리 안 들어감, 빌드할 때마다 재생성).

## 5. Build & Packaging

### 5.1 PyInstaller

**스펙 파일:** `tray_client/build/irms_notice.spec`

```python
a = Analysis(
    ["../src/main.py"],
    pathex=["."],
    datas=[
        ("../assets/icon.ico", "assets"),
        ("../assets/ding.wav", "assets"),
    ],
    hiddenimports=["pyttsx3.drivers.sapi5", "pywintypes", "pythoncom"],
    ...
)
exe = EXE(
    ...,
    name="IRMS-Notice",
    console=False,          # 콘솔 창 안 뜨게
    icon="../assets/icon.ico",
)
```

**산출물:** `dist/IRMS-Notice/IRMS-Notice.exe` + 의존 DLL 폴더 (one-folder 모드 권장, 기동 속도 빠름)

### 5.2 Inno Setup

**스크립트:** `tray_client/build/installer.iss`

**주요 설정:**
- 설치 경로: `C:\Program Files\IRMS-Notice\`
- 시작 메뉴 바로가기 + 바탕화면 바로가기(옵션)
- **시작프로그램 등록:** `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`에 값 추가
- 언인스톨러 자동 생성
- 한국어 UI
- Windows 10 이상 요구

**산출물:** `Output/IRMS-Notice-Setup-v1.0.exe` (단일 파일 설치 마법사)

### 5.3 빌드 스크립트

**`tray_client/build/build.bat`:**
```
python src\assets_gen.py          (아이콘, wav 생성)
pyinstaller build\irms_notice.spec
iscc build\installer.iss
echo Build complete: Output\IRMS-Notice-Setup-v1.0.exe
```

## 6. Error Handling

| 상황 | 처리 |
|------|------|
| 서버 다운 | 지수 백오프 5→10→20→40→60초, 트레이 상태 "오프라인" |
| 응답 JSON 파싱 실패 | 로그 기록, 해당 루프 스킵, 다음 주기에 재시도 |
| TTS 엔진 초기화 실패 | 로그 + 트레이 알림 "TTS 사용 불가", 효과음만 재생 |
| `config.json` 손상 | 백업 후 기본값으로 리셋 |
| 네트워크 IP 변경 (PC가 다른 망으로) | 서버가 403 → 트레이 상태 "접속 거부", 백오프 유지 |
| 멀티 인스턴스 실행 | 뮤텍스(`CreateMutexW`) 체크 → 이미 실행 중이면 기존 창 포커스 후 종료 |

## 7. Security Considerations

- **읽기 전용 API:** poll/ping만 존재, 쓰기/삭제 불가
- **내부망 전용:** IP 화이트리스트로 외부 접근 차단
- **메시지 누출 범위:** 공지방(notice)만 노출, workflow방은 기존 인증 경로 유지
- **TTS 내용:** 공지방 메시지는 원래 전 직원 공개 의도이므로 음성 재생에 문제 없음
- **바이너리 배포:** 코드 서명 없이 내부 배포. SmartScreen 경고 시 관리자가 "추가 정보 → 실행" 안내
- **config.json:** 평문 저장 (민감 정보 없음, 서버 URL과 상태값만)

## 8. Test Plan

**서버 측 (단위):**
1. `GET /api/public/notice/ping` → 200 OK
2. `GET /api/public/notice/poll?after_id=0` → `items=[]`, `latest_id` 반환
3. 공지 1건 등록 → `after_id=직전` → `items=[1]`
4. 외부 IP로 접근 → 403

**클라이언트 측 (수동):**
1. 설치 → 트레이 아이콘 표시 확인
2. 서버 다운 상태 → "오프라인" 표시, 복구 시 자동 재연결
3. 공지 전송 → 5초 이내 효과음 + TTS 재생
4. 음소거 토글 → 새 공지 무시 확인
5. "테스트 재생" 메뉴 → 샘플 음성 재생
6. 종료 → 트레이 아이콘 사라짐, 프로세스 종료
7. PC 재부팅 → 자동 시작 확인

**현장 통합:**
1. 7대 PC 동시 설치 후 공지 1건 전송 → 7대 동시(±5초) 재생 확인
2. 서버 재시작 중 공지 누락 여부 확인

## 9. Non-Goals (명시적 제외)

- 양방향 채팅 (읽기 전용)
- 로그인/인증 (내부망이므로 불필요)
- 메시지 히스토리 UI (트레이 앱에서 목록 조회 없음)
- macOS/Linux 지원
- 자동 업데이트 (v1 수동)
- 푸시 알림 (Windows Toast) — v1에서는 소리만, 향후 고려
- 서버 주소 변경 GUI — `config.json` 수동 편집

## 10. Rollout Plan

1. 서버 API + 미들웨어 구현 → 1대 PC로 테스트
2. 트레이 클라이언트 구현 → 개발 PC에서 `python src/main.py`로 동작 확인
3. PyInstaller 빌드 → `.exe` 단독 실행 확인
4. Inno Setup 빌드 → 설치 파일로 1대 설치 테스트
5. **1대 현장 PC 시범 설치** (1~2일 운영 → 이슈 수집)
6. 나머지 6대 배포
7. 운영 매뉴얼 배포 (음소거/재시작/문의 경로)

## 11. Next Step

구현 시작 — 서버 API → 트레이 클라이언트 → 빌드 순.
