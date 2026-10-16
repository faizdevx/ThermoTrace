"""Security building blocks that need no database: password hashing and policy, rate limiter, client address, audit canonical form."""
from __future__ import annotations

from datetime import datetime, timezone

from backend.app.security import audit, passwords, rank
from backend.app.security.ratelimit import RateLimiter, client_ip


def test_scrypt_hashes_are_salted_and_verified_in_constant_form():
    a, b = passwords.hash_password("correct horse battery", n=2 ** 10), passwords.hash_password("correct horse battery", n=2 ** 10)
    assert a != b and a.startswith("scrypt$1024$8$1$")
    assert passwords.verify_password("correct horse battery", a) and not passwords.verify_password("correct horse batterx", a)
    assert not passwords.verify_password("x", "not-a-hash") and not passwords.verify_password("x", "bcrypt$1$2$3$4$5")


def test_password_policy():
    assert passwords.check_policy("short1") and "12 characters" in passwords.check_policy("short1")
    assert "username" in passwords.check_policy("asha-is-my-name-1", "asha")
    assert passwords.check_policy("aaaaaaaaaaaaaaaa") == "password is too repetitive"
    assert passwords.check_policy("changeme1234")
    assert passwords.check_policy("Tr1cky-Pass-Phrase!") is None


def test_roles_are_ordered():
    assert rank("viewer") < rank("analyst") < rank("admin") and rank(None) == -1 and rank("root") == -1


def test_rate_limiter_counts_per_rule_and_key_and_recovers():
    t = [0.0]
    rl = RateLimiter(clock=lambda: t[0])
    assert [rl.check("login", "1.1.1.1", 3)[0] for _ in range(4)] == [True, True, True, False]
    assert rl.check("login", "2.2.2.2", 3)[0] and rl.check("other", "1.1.1.1", 3)[0]     # other key / other rule: own budget
    ok, retry = rl.check("login", "1.1.1.1", 3)
    assert not ok and 0 < retry <= 60
    t[0] = 61.0
    assert rl.check("login", "1.1.1.1", 3)[0]


def test_client_ip_trusts_only_the_configured_proxy_hops():
    h = {"x-forwarded-for": "6.6.6.6, 10.1.1.1, 172.18.0.5"}
    assert client_ip(h, "172.18.0.9", 0) == "172.18.0.9"            # no trusted proxy: the header can be forged, ignore it
    assert client_ip(h, "172.18.0.9", 1) == "172.18.0.5"
    assert client_ip(h, "172.18.0.9", 2) == "10.1.1.1"
    assert client_ip({}, None, 1) == "unknown"


def test_audit_canonical_form_is_stable_and_chain_links_content():
    at = datetime(2026, 10, 16, 12, 0, 0, 123456, tzinfo=timezone.utc)
    c1 = audit.canonical(at, "asha", "analyst", "alert.decision", "7", {"b": 1, "a": [2, 3]}, "10.0.0.1")
    c2 = audit.canonical(at, "asha", "analyst", "alert.decision", "7", {"a": [2, 3], "b": 1}, "10.0.0.1")
    assert c1 == c2 and "12:00:00.123456" in c1
    h1 = audit.chain_hash(audit.GENESIS, c1)
    assert h1 != audit.chain_hash("other", c1) and h1 != audit.chain_hash(audit.GENESIS, c1.replace("asha", "bob"))
