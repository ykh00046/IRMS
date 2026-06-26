# BRM (Blend & Recipe Management) — 배합·레시피 관리

> 배합·레시피 관리, 계량, 재고, 근태 기능을 갖춘 FastAPI 웹 애플리케이션.
> (구 IRMS. 화면 브랜드는 BRM이지만 **내부 코드 식별자는 IRMS 유지** — `window.IRMS`
>  네임스페이스, `IRMS_ENV`·`IRMS_DATA_DIR` 등 환경변수, 배치파일. 리네이밍 위험으로 보존.)

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

# 또는 배치 파일
run_irms.bat              # 일반 실행
run_irms_intranet.bat     # 인트라넷 실행 (reload 없음)

# 터널 실행 (외부 접근)
run_tunnel.bat
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
├── auth.py              # 인증 로직
├── security.py          # 보안 헬퍼
├── limiter.py           # Rate limiter (slowapi)
├── attendance_auth.py   # 출입관리 인증
├── db/
│   ├── connection.py    # DB 연결
│   ├── schema.py        # 스키마 정의
│   ├── migrations.py    # 마이그레이션
│   ├── queries.py       # 쿼리 헬퍼
│   ├── seeds.py         # 시드 데이터
│   ├── audit.py         # 감사 로그
│   └── time_utils.py    # 시간 유틸
├── routers/
│   ├── pages.py         # 페이지 라우트 (HTML)
│   ├── api.py           # API 라우트 (JSON)
│   ├── auth_routes.py   # 인증 라우트
│   ├── admin_routes.py  # 관리자 라우트
│   ├── dashboard_routes.py # 대시보드
│   ├── recipe_manager_routes.py  # 레시피 관리
│   ├── recipe_operator_routes.py # 레시피 운영
│   ├── recipe_stats_routes.py    # 레시피 통계
│   ├── recipe_import_routes.py   # 레시피 가져오기
│   ├── spreadsheet_routes.py     # 스프레드시트
│   ├── weighing_routes.py        # 계량
│   ├── stock_routes.py           # 재고
│   ├── attendance_routes.py      # 출입관리
│   ├── chat_routes.py            # 채팅
│   └── models.py        # Pydantic 모델
├── services/
│   ├── attendance_excel.py       # 출입 Excel 처리
│   ├── import_parser.py          # 가져오기 파서
│   ├── material_resolver.py      # 자재 해석
│   ├── recipe_helpers.py         # 레시피 헬퍼
│   ├── stock_service.py          # 재고 서비스
│   └── cell_value_parser.py      # 셀 값 파서
└── middleware/
    ├── internal_only.py          # 내부망 접근 제한
    └── security_headers.py       # 보안 헤더

templates/              # Jinja2 HTML 템플릿
├── _base_app.html      # 공통 베이스
├── base.html           # 일반 베이스
├── entry.html          # 레시피 입력
├── management.html     # 관리 페이지
├── insight.html        # 분석 페이지
├── dashboard.html      # 대시보드
├── weighing_select.html# 계량 선택
├── login.html          # 로그인
├── attendance.html     # 출입관리
└── work.html           # 작업

static/                 # 정적 파일 (css, js, vendor)
tests/                  # pytest 테스트
data/                   # SQLite DB (런타임)
scripts/                # 유틸리티 스크립트
tools/                  # 부트스트랩/스모크 도구
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
- **계량 모드**: 수동 진행 (저울 연계 없음)
- **환경변수**: `IRMS_ENV`, `IRMS_SESSION_SECRET`, `IRMS_DATA_DIR`, `IRMS_SEED_DEMO_DATA`
- **세션**: `SessionMiddleware` + CSRF 보호 (`starlette-csrf`)
- **Rate Limiting**: `slowapi` 적용
- **운영 환경**: `IRMS_ENV=production` 시 보안 강화 (HSTS, Secure 쿠키, Strict SameSite)
- **데모 데이터**: 개발 시 `IRMS_SEED_DEMO_DATA=1`로 자동 생성 (운영에서는 반드시 `0`)
- **Cloudflare Tunnel**: `cloudflared/` + `setup_tunnel.bat`으로 외부 접근 가능
- **런타임 산출물**: `tmp_*`, `data/`는 gitignore, 소스 기준 아님
- **트레이 클라이언트**: `tray_client/`에 Windows 알림 트레이 앱 존재
