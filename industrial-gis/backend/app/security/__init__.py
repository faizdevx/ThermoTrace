"""Authentication, roles, audit log, rate limiting and request logging."""
ROLES = ("viewer", "analyst", "admin")


def rank(role: str | None) -> int:
    return ROLES.index(role) if role in ROLES else -1
