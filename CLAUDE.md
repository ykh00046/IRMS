# BRM (Blend & Recipe Management) — 배합·레시피 관리

> 배합 실적(DHR)·레시피 관리, 점도 분석, 근태 기능을 갖춘 FastAPI 웹 애플리케이션.
> (구 IRMS. 화면 브랜드는 BRM이지만 **내부 코드 식별자는 IRMS 유지** — `window.IRMS`
>  네임스페이스, `IRMS_ENV`·`IRMS_DATA_DIR` 등 환경변수, 배치파일. 리네이밍 위험으로 보존.)
> UI·대화에서 '잉크/ink' 단어 금지(내부 컬럼 ink_name 은 잔존 허용). 재고/발주/채팅 기능은 제거됨.

## Project Level
**Level: Dynamic** (FastAPI 웹서버 + SQLite + Jinja2 템플릿 + 인증/세션 + Cloudflare Tunnel)

## Quick Start

```bash
# 설치
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 부트스트랩 + smoke 테스트
python tools/bootstrap_irms.py --run-smoke

# 실행 (개발)
uvicorn src.main:app --reload
# 브라우저: http://127.0.0.1:8000/

# 운영 PC (포트 9000, 창 하나로 서버 + 자동 업데이트)
run_auto.bat              # serve.py — 주기적 git pull 감시 후 자동 재시작

# 기타 배치 파일
run_irms.bat              # 일반 실행
run_irms_intranet.bat     # 인트라넷 실행 (reload 없음)
run_tunnel.bat            # 터널 실행 (외부 접근)
```

## Architecture

```
┌──────────────┐     ┌──────────────────┐     ┌──────────┐
│  Browser     │────▶│  FastAPI App     │────▶│  SQLite  │
│  (Jinja2)    │     │  src/main.py     │     │  data/   │
│              │◀────│  (routers/)      │     │          │
└──────────────┘     └──────────────────┘     └──────────┘
                            │
                     ┌──────▼──────┐
                     │  Middleware  │
                     │  - Session   │
                     │  - CSRF      │
                     │  - Rate Limit│
                     │  - Security  │
                     └─────────────┘
```

### 주요 디렉토리 구조

```
src/
├── main.py              # FastAPI 앱 팩토리 (create_app)
├── config.py            # 환경 설정 (IRMS_ENV, SESSION_SECRET 등)
├── auth.py              # 인증 (이름 기반 책임자(workers 명단) + admin/admin 폴백, 권한 2단계)
├── blend_session.py     # 배합 작업자 세션 (이름 기반)
├── attendance_auth.py   # 근태 인증 (사번+비번, 자체 세션)
├── security.py          # 보안 헬퍼 (CSRF 등)
├── limiter.py           # Rate limiter (slowapi)
├── db/                  # connection/schema/migrations/queries/seeds/audit
├── routers/
│   ├── pages.py         # 페이지 라우트 (HTML)
│   ├── api.py           # API 라우터 조립 (모든 하위 라우터 include)
│   ├── auth_routes.py / admin_routes.py     # 로그인·비번변경 · 감사로그/서명/시트백업(책임자 전용)
│   ├── blend_routes.py                      # 배합 실적·DHR·점도연계·next-lot
│   ├── blend_session_routes.py / worker_routes.py  # 작업자 세션·명단
│   ├── viscosity_routes.py                  # 점도 등록·분석·반제품 설정
│   ├── recipe_{manager,operator,import}_routes.py  # 레시피
│   ├── dashboard_routes.py                  # 대시보드
│   ├── attendance_routes.py                 # 근태 (⚠ future annotations 금지 — 상단 주석)
│   ├── public_{attendance_alert,viscosity_reminder}_routes.py  # 트레이용 내부망 공개 API
│   └── models.py        # Pydantic 모델   (구 계량 API 는 제거 — /weighing 페이지는 /blend 리다이렉트만 잔존)
├── services/            # blend/viscosity/attendance_excel/dhr_{excel,pdf,cache}/
│                        # signature_*/record_delete/variance/worker/import_parser 등
└── middleware/          # internal_only(내부망 제한) · security_headers

templates/              # Jinja2 (_base_app.html 상속)
├── entry.html          # 홈 런처 (근태/반제품 제조 게이트)
├── blend.html          # 배합 실적 입력 (+blend_login.html)
├── status.html         # 배합 기록 · DHR 출력
├── viscosity.html      # 점도 등록·추세·이상 분석
├── management.html     # 레시피 관리 (+management_login.html)
├── insight.html        # 배합 분석 (자재별 사용량 + 제품별 빈도·배치 상세)
├── dashboard.html      # 운영 대시보드
├── admin_users.html    # 사용자 관리 — 이용자 명단·책임자 지정·감사로그 (책임자 전용)
└── attendance*.html    # 근태 (메인/로그인/비번변경)

static/                 # 정적 파일 (css, js, vendor) — ?v= 캐시버스팅 필수
tests/                  # pytest (in-memory SQLite 픽스처)
tray_client/            # Windows 트레이 앱 (근태·점도 리마인더 + 저울 연동, 기능별 토글)
scale_agent/            # A&D 저울 로컬 HTTP 에이전트 (127.0.0.1:8787 — 배합 화면 연동)
serve.py / run_auto.bat # 운영: 단일 창 서버 + 자동 git pull 업데이트
data/                   # SQLite DB (런타임, gitignore)
scripts/ tools/         # 유틸리티 · 부트스트랩/스모크
```

## Key Commands

| 작업 | 명령어 |
|------|--------|
| 서버 실행 (개발) | `uvicorn src.main:app --reload` |
| 서버 실행 (배치) | `run_irms.bat` |
| 인트라넷 실행 | `run_irms_intranet.bat` |
| 터널 실행 | `run_tunnel.bat` |
| 부트스트랩 + smoke | `python tools/bootstrap_irms.py --run-smoke` |
| 테스트 | `python -m pytest tests -v` |
| smoke (개발) | `python tools/smoke_irms.py --mode development --seed-demo-data` |
| smoke (운영) | `python tools/smoke_irms.py --mode production --session-secret '...'` |

## Coding Conventions

- **Python 3.11+** 권장
- **snake_case**: 함수, 변수, 모듈
- **PascalCase**: 클래스
- FastAPI 라우터는 `routers/`에 파일별 분리
- 서비스 로직은 `services/`, DB는 `db/`에 격리
- Jinja2 템플릿: `_base_app.html` / `base.html` 상속 구조
- 환경 설정: `.env` + `src/config.py` (환경변수 우선)
- `conftest.py`에서 테스트 환경변수 강제 설정 (`IRMS_ENV=test`)

## Important Notes

- **모든 레시피 값 단위**: `g` 고정
- **계량 모드**: 수동 입력 기본 + A&D 저울 연동 옵션 (scale_agent/트레이 앱이 로컬 127.0.0.1:8787 HTTP 로 배합 화면에 무게 전달)
- **환경변수**: `IRMS_ENV`, `IRMS_SESSION_SECRET`, `IRMS_DATA_DIR`, `IRMS_SEED_DEMO_DATA`
- **세션**: `SessionMiddleware` + CSRF 보호 (`starlette-csrf`)
- **Rate Limiting**: `slowapi` 적용
- **운영 환경**: `IRMS_ENV=production` 시 보안 강화 (HSTS, Secure 쿠키, Strict SameSite)
- **데모 데이터**: 개발 시 `IRMS_SEED_DEMO_DATA=1`로 자동 생성 (운영에서는 반드시 `0`)
- **Cloudflare Tunnel**: `cloudflared/` + `setup_tunnel.bat`으로 외부 접근 가능
- **런타임 산출물**: `tmp_*`, `data/`는 gitignore, 소스 기준 아님
- **트레이 클라이언트**: `tray_client/`에 Windows 알림 트레이 앱 존재
- **DB 백업**: serve.py 가 매일 1회 + 업데이트 직전 자동 백업(`backups/irms_*.db`,
  SQLite 온라인 백업, 보존 `IRMS_BACKUP_KEEP_DAYS`=30일·최근 5개 항상 유지,
  `IRMS_BACKUP_MIRROR`로 2차 사본 폴더 지정 가능).
  **복구**: 서버 중지 → `backups/`의 원하는 파일을 `data/irms.db`로 복사 → 서버 시작
- **의존성 잠금**: 운영은 `requirements-lock.txt`(고정 버전) 우선 설치 — 무통제 업그레이드
  방지. 업그레이드 절차: 개발 PC에서 `pip install -r requirements.txt` → 전체 테스트/smoke
  통과 → `pip freeze > requirements-lock.txt` 커밋
