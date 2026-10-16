"""Password hashing with scrypt (standard library) and the password policy."""
from __future__ import annotations

import base64
import hashlib
import hmac
import secrets

N, R, P = 2 ** 15, 8, 1
MIN_LENGTH = 12
_MAXMEM = 128 * N * R * 2 + 2 ** 20


def hash_password(password: str, *, n: int = N) -> str:
    salt = secrets.token_bytes(16)
    dk = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=n, r=R, p=P, maxmem=_MAXMEM, dklen=32)
    return "scrypt${}${}${}${}${}".format(n, R, P, base64.b64encode(salt).decode(), base64.b64encode(dk).decode())


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, n, r, p, salt, dk = stored.split("$")
        if scheme != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), maxmem=_MAXMEM, dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(dk))
    except (ValueError, TypeError):
        return False


# an unknown user costs the same time as a wrong password, so response time does not reveal which usernames exist
_DUMMY = hash_password("not-a-real-password-for-timing")


def spend_time() -> None:
    verify_password("x", _DUMMY)


def check_policy(password: str, username: str = "") -> str | None:
    """None when acceptable, else the reason."""
    if len(password) < MIN_LENGTH:
        return f"password must be at least {MIN_LENGTH} characters"
    if username and username.lower() in password.lower():
        return "password must not contain the username"
    if len(set(password)) < 5:
        return "password is too repetitive"
    if password.lower() in {"password1234", "123456789012", "qwertyuiop12", "changeme1234"}:
        return "password is on the list of common passwords"
    return None
