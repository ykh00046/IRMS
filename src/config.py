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
SESSION_SECRET = os.getenv("IRMS_SESSION_SECRET")
REQUIRE_SESSION_SECRET = _env_flag("IRMS_REQUIRE_SESSION_SECRET", not IS_DEVELOPMENT)
SEED_DEMO_DATA = _env_flag("IRMS_SEED_DEMO_DATA", IS_DEVELOPMENT)

if REQUIRE_SESSION_SECRET and not SESSION_SECRET:
    raise RuntimeError(
        "IRMS_SESSION_SECRET must be set when IRMS_REQUIRE_SESSION_SECRET is enabled."
    )

if not SESSION_SECRET:
    SESSION_SECRET = secrets.token_hex(32)
