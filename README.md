# IRMS

IRMS(Ink Recipe Management System) PoC 구현입니다.

## Run

```bash
python tools/bootstrap_irms.py --run-smoke
source .venv/bin/activate  # Windows: .venv\\Scripts\\activate
uvicorn src.main:app --reload
```

Windows quick start:

```bat
python tools\\bootstrap_irms.py --run-smoke
run_irms.bat
```

Windows intranet run (no auto-reload):

```bat
python tools\\bootstrap_irms.py
run_irms_intranet.bat
```

브라우저:

- `http://127.0.0.1:8000/`
- `http://127.0.0.1:8000/management`
- `http://127.0.0.1:8000/insight`

운영 기준:

- 모든 레시피 값 단위는 `g` 고정
- 계량 모드는 수동 진행 (저울 연계 없음)

## Environment

- `IRMS_ENV`: 기본값 `development`. `production` 등 비개발 값에서는 보안 기본값이 더 엄격해집니다.
- `IRMS_SESSION_SECRET`: 세션 서명 키. 운영 경로에서는 반드시 설정해야 합니다.
- `IRMS_REQUIRE_SESSION_SECRET`: `1`이면 `IRMS_SESSION_SECRET` 미설정 시 부팅 실패.
- `IRMS_SEED_DEMO_DATA`: `1`이면 데모 계정/샘플 데이터를 자동 생성. 개발 기본값은 `1`, 비개발 기본값은 `0`.
- `IRMS_DATA_DIR`: 런타임 SQLite 경로. 기본값은 프로젝트 내부 `data/`.

운영 예시:

```bash
export IRMS_ENV=production
export IRMS_SESSION_SECRET='replace-with-real-secret'
export IRMS_DATA_DIR=/var/lib/irms
uvicorn src.main:app --host 0.0.0.0 --port 8000
```

## Runtime Artifact Policy

- 운영 경로에서는 `IRMS_DATA_DIR`를 프로젝트 바깥 런타임 디렉터리로 지정하는 것을 권장합니다.
- `data/`는 로컬 개발용 기본값으로만 사용합니다.
- `tmp_*`, `tmp_e2e_artifacts/`, `tmp_e2e_runtime/`, `__pycache__/` 같은 디렉터리는 런타임/테스트 산출물이며 소스 기준으로 취급하지 않습니다.
- `.gitignore`는 위 런타임 산출물과 SQLite WAL 파일을 무시하도록 정리했습니다.

## Smoke Check

Bootstrap + smoke in one step:

```bash
python tools/bootstrap_irms.py --run-smoke
```

Options:

- `--venv-dir <path>`: create/use a different virtual environment path
- `--skip-install`: reuse an existing environment without reinstalling packages
- `--run-smoke`: run `tools/smoke_irms.py` inside the bootstrapped environment after install

개발 smoke:

```bash
python tools/smoke_irms.py --mode development --seed-demo-data
```

운영 smoke:

```bash
python tools/smoke_irms.py \
  --mode production \
  --session-secret 'replace-with-real-secret' \
  --data-dir ./tmp_smoke_runtime \
  --check-health \
  --clean
```

이 스크립트는 다음을 확인합니다.

- `src/` 파이썬 파일 `py_compile`
- 앱 import 및 `create_app()` 성공
- `/health` 라우트 존재 여부
- 선택 시 `/health` 요청 응답
