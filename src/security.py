import hashlib
import hmac
import secrets


PASSWORD_ALGORITHM = "pbkdf2_sha256"
PASSWORD_ITERATIONS = 200_000


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
