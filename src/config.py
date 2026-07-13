import os
import secrets
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
except ImportError:
    pass


def _env_flag(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


BASE_DIR = Path(__file__).resolve().parent.parent
APP_ENV = os.getenv("IRMS_ENV", "development").strip().lower()
IS_DEVELOPMENT = APP_ENV in {"dev", "development", "local", "test"}
DATA_DIR = Path(os.getenv("IRMS_DATA_DIR", str(BASE_DIR / "data"))).expanduser().resolve()
DATABASE_PATH = DATA_DIR / "irms.db"
SESSION_COOKIE_NAME = "irms_session"
SESSION_MAX_AGE = int(os.getenv("IRMS_SESSION_MAX_AGE", str(60 * 60 * 8)))
# 책임자(로그인 권한) 세션 유휴 만료. 공용 PC 에서 자리를 뜨면 레시피·기록 삭제,
# 사용자 관리, 감사로그 권한이 열린 채 방치되므로 짧게 끊는다. 배합 작업자
# 세션(이름만·무비번)은 계량 중 끊기면 안 되므로 별도 규칙([[blend_session]]).
MANAGER_IDLE_TIMEOUT_SECONDS = int(os.getenv("IRMS_MANAGER_IDLE_TIMEOUT", str(60 * 15)))
SESSION_SECRET = os.getenv("IRMS_SESSION_SECRET")
REQUIRE_SESSION_SECRET = _env_flag("IRMS_REQUIRE_SESSION_SECRET", not IS_DEVELOPMENT)
SEED_DEMO_DATA = _env_flag("IRMS_SEED_DEMO_DATA", IS_DEVELOPMENT)
TRAY_API_TOKEN = os.getenv("IRMS_TRAY_API_TOKEN", "").strip()
REQUIRE_TRAY_API_TOKEN = _env_flag(
    "IRMS_REQUIRE_TRAY_API_TOKEN", not IS_DEVELOPMENT
)

if REQUIRE_SESSION_SECRET and not SESSION_SECRET:
    raise RuntimeError(
        "IRMS_SESSION_SECRET must be set when IRMS_REQUIRE_SESSION_SECRET is enabled."
    )

if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)

if REQUIRE_TRAY_API_TOKEN and not TRAY_API_TOKEN:
    raise RuntimeError(
        "IRMS_TRAY_API_TOKEN must be set when IRMS_REQUIRE_TRAY_API_TOKEN is enabled."
    )
