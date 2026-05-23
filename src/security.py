import hashlib
import hmac
import secrets

from itsdangerous import URLSafeSerializer

from .config import IS_DEVELOPMENT, SESSION_SECRET

PASSWORD_ALGORITHM="***"
PASSWORD_ITERATIONS=***

# 모듈 로드 시 한 번만 생성 (매 호출마다 재생성 방지)
_csrf_serializer = URLSafeSerializer(SESSION_SECRET, "csrftoken")


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        PASSWORD_ITERATIONS,
    ).hex()
    return f"{PASSWORD_ALGORITHM}${PASSWORD_ITERATIONS}${salt}${digest}"


def verify_password(password: str, encoded_password: str) -> bool:
    try:
        algorithm, iterations_text, salt, expected_digest = encoded_password.split("$", 3)
    except ValueError:
        return False

    if algorithm != PASSWORD_ALGORITHM:
        return False

    try:
        iterations = int(iterations_text)
    except ValueError:
        return False

    actual_digest = hashlib.pbkdf2_hmac(
        "sha256",
        password.encode("utf-8"),
        salt.encode("utf-8"),
        iterations,
    ).hex()
    return hmac.compare_digest(actual_digest, expected_digest)


def refresh_csrf_cookie(response) -> None:
    response.set_cookie(
        "csrftoken",
        _csrf_serializer.dumps(secrets.token_urlsafe(32)),
        path="/",
        secure=not IS_DEVELOPMENT,
        httponly=False,
        samesite="lax",
    )
