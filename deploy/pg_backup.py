"""PostGIS backup, verify and restore for the ThermoTrace stack.

    python deploy/pg_backup.py backup  [--out backups] [--keep 14] [--db NAME] [--passphrase-env BACKUP_PASSPHRASE]
    python deploy/pg_backup.py verify  backups/thermotrace-...dump[.enc]      # checksum + restore into a scratch database + compare row counts
    python deploy/pg_backup.py restore backups/thermotrace-...dump[.enc] --db NAME [--into-live]
    python deploy/pg_backup.py list    [--out backups]

A backup is one custom-format ``pg_dump`` of the whole database (accounts, sessions, audit log, alerts, labels, facilities, thermal
tables, the archived detections) plus ``<file>.manifest.json`` with the SHA-256 of the dump, the row counts of the key tables, and
the PostgreSQL version. With ``--passphrase-env`` the dump is encrypted (AES-256-CBC, PBKDF2) with the passphrase held in that
environment variable. ``restore`` never overwrites the live database unless ``--into-live`` is given, and it checks the checksum first.

The database runs in the compose stack; point at another stack with ``BACKUP_COMPOSE_ARGS="-p other -f docker-compose.yml"``.
Not in the backup: ``.env`` (secrets), the Docker volumes of caches (land cover, imagery, snapshots: rebuilt while online), the
basemap file. Back up ``.env`` and ``data/basemap`` separately.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import secrets
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY_TABLES = ["states", "districts", "facilities", "facility_sources", "thermal_sources", "thermal_detections", "thermal_runs", "alerts",
              "alert_decisions", "labels", "users", "audit_log", "thermal_model"]
PREFIX = "thermotrace"


def compose_base() -> list[str]:
    return ["docker", "compose", *os.getenv("BACKUP_COMPOSE_ARGS", "").split()]


def env_values() -> dict[str, str]:
    out: dict[str, str] = {}
    f = ROOT / ".env"
    if f.exists():
        for line in f.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip()
    return out


def db_exec(*args: str, db: str | None = None, stdin=None, stdout=None, capture=False, check=True) -> subprocess.CompletedProcess:
    cmd = [*compose_base(), "exec", "-T", "db", *args]
    return subprocess.run(cmd, cwd=ROOT, stdin=stdin, stdout=stdout, capture_output=capture, check=check)


def psql(sql: str, db: str, user: str) -> str:
    return db_exec("psql", "-U", user, "-d", db, "-At", "-v", "ON_ERROR_STOP=1", "-c", sql, capture=True).stdout.decode().strip()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def counts(db: str, user: str) -> dict[str, int | None]:
    out: dict[str, int | None] = {}
    for t in KEY_TABLES:
        try:
            out[t] = int(psql(f"SELECT count(*) FROM {t}", db, user))
        except subprocess.CalledProcessError:
            out[t] = None                                    # table not in this schema version
    return out


def openssl(mode: str, src: Path, dst: Path, passphrase_env: str) -> None:
    if not os.getenv(passphrase_env):
        raise SystemExit(f"environment variable {passphrase_env} is empty")
    flag = "-e" if mode == "enc" else "-d"
    r = subprocess.run(["openssl", "enc", flag, "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", f"env:{passphrase_env}", "-in", str(src), "-out", str(dst)],
                       capture_output=True)
    if r.returncode != 0:
        raise SystemExit(f"openssl could not {'encrypt' if mode == 'enc' else 'decrypt'} the file" + (" (wrong passphrase?)" if mode == "dec" else ""))


def backup(a) -> Path:
    ev = env_values()
    user, db = ev.get("POSTGRES_USER", "thermotrace"), a.db or ev.get("POSTGRES_DB", "thermotrace")
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    dump = out / f"{PREFIX}-{db}-{stamp}.dump"
    with dump.open("wb") as f:
        db_exec("pg_dump", "-U", user, "-d", db, "-Fc", "--no-owner", stdout=f)
    manifest = {"created_at": datetime.now(timezone.utc).isoformat(), "database": db, "postgres": psql("SELECT version()", db, user),
                "row_counts": counts(db, user), "encrypted": False, "format": "pg_dump custom (-Fc)"}
    final = dump
    if a.passphrase_env:
        final = dump.with_name(dump.name + ".enc")
        openssl("enc", dump, final, a.passphrase_env)
        dump.unlink()
        manifest["encrypted"] = "aes-256-cbc-pbkdf2"
    manifest.update({"file": final.name, "size_bytes": final.stat().st_size, "sha256": sha256_file(final)})
    (final.with_name(final.name + ".manifest.json")).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"backup {final} ({manifest['size_bytes'] / 1e6:.1f} MB, sha256 {manifest['sha256'][:16]}...)")
    prune(out, a.keep, db)
    return final


def prune(out: Path, keep: int, db: str) -> None:
    dumps = sorted(out.glob(f"{PREFIX}-{db}-*.dump*"), key=lambda p: p.name)
    dumps = [p for p in dumps if not p.name.endswith(".manifest.json")]
    for old in dumps[:-keep] if keep > 0 else []:
        old.unlink()
        old.with_name(old.name + ".manifest.json").unlink(missing_ok=True)
        print("pruned", old.name)


def load_manifest(path: Path) -> dict:
    m = path.with_name(path.name + ".manifest.json")
    if not m.exists():
        raise SystemExit(f"no manifest next to {path.name}: refusing to restore an unverifiable backup")
    return json.loads(m.read_text(encoding="utf-8"))


def check_file(path: Path, man: dict) -> None:
    got = sha256_file(path)
    if got != man["sha256"]:
        raise SystemExit(f"CHECKSUM MISMATCH for {path.name}: manifest {man['sha256'][:16]}..., file {got[:16]}... - the backup is damaged or was changed")
    print("checksum ok")


def plain_dump(path: Path, man: dict, passphrase_env: str | None, tmp: Path) -> Path:
    if not man.get("encrypted"):
        return path
    if not passphrase_env:
        raise SystemExit("this backup is encrypted: give --passphrase-env")
    dst = tmp / path.name.removesuffix(".enc")
    openssl("dec", path, dst, passphrase_env)
    return dst


def restore_into(dump: Path, db: str, user: str) -> None:
    exists = psql(f"SELECT 1 FROM pg_database WHERE datname = '{db}'", "postgres", user)
    if not exists:
        db_exec("psql", "-U", user, "-d", "postgres", "-v", "ON_ERROR_STOP=1", "-c", f'CREATE DATABASE "{db}"')
    with dump.open("rb") as f:
        r = db_exec("pg_restore", "-U", user, "-d", db, "--no-owner", "--exit-on-error", stdin=f, check=False)
    if r.returncode != 0:
        raise SystemExit(f"pg_restore failed (exit {r.returncode})")


def restore(a, scratch: bool = False) -> None:
    ev = env_values()
    user, live = ev.get("POSTGRES_USER", "thermotrace"), ev.get("POSTGRES_DB", "thermotrace")
    path = Path(a.file)
    man = load_manifest(path)
    check_file(path, man)
    if a.db == live and not a.into_live:
        raise SystemExit(f"{a.db} is the live database: restoring over it needs --into-live (stop the api and the thermal worker first)")
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        dump = plain_dump(path, man, a.passphrase_env, Path(td))
        if a.db == live:
            db_exec("psql", "-U", user, "-d", "postgres", "-c", f'DROP DATABASE IF EXISTS "{live}" WITH (FORCE)')
        restore_into(dump, a.db, user)
    got = counts(a.db, user)
    bad = {t: (man["row_counts"].get(t), n) for t, n in got.items() if man["row_counts"].get(t) is not None and man["row_counts"][t] != n}
    if bad:
        raise SystemExit(f"row counts differ after restore (table: backup, restored): {bad}")
    print(f"restored into {a.db}; row counts match the manifest for {sum(v is not None for v in got.values())} tables")


def verify(a) -> None:
    ev = env_values()
    user = ev.get("POSTGRES_USER", "thermotrace")
    scratch = f"tt_verify_{secrets.token_hex(4)}"
    a.db, a.into_live = scratch, False
    try:
        restore(a)
        print("VERIFIED: checksum ok, restores cleanly, row counts match")
    finally:
        db_exec("psql", "-U", user, "-d", "postgres", "-c", f'DROP DATABASE IF EXISTS "{scratch}" WITH (FORCE)', check=False)


def list_backups(a) -> None:
    for p in sorted(Path(a.out).glob(f"{PREFIX}-*.dump*")):
        if p.name.endswith(".manifest.json"):
            continue
        man = json.loads(p.with_name(p.name + ".manifest.json").read_text(encoding="utf-8")) if p.with_name(p.name + ".manifest.json").exists() else {}
        print(f"{p.name:<60} {p.stat().st_size / 1e6:9.1f} MB  {man.get('created_at', '(no manifest)')[:19]}  {'encrypted' if man.get('encrypted') else ''}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["backup", "verify", "restore", "list"])
    ap.add_argument("file", nargs="?")
    ap.add_argument("--out", default="backups")
    ap.add_argument("--keep", type=int, default=14, help="backups to keep per database (0 = keep all)")
    ap.add_argument("--db")
    ap.add_argument("--into-live", action="store_true")
    ap.add_argument("--passphrase-env", help="name of the environment variable holding the encryption passphrase")
    a = ap.parse_args(argv)
    if a.command == "backup":
        backup(a)
    elif a.command == "list":
        list_backups(a)
    else:
        if not a.file:
            raise SystemExit("give the backup file")
        if a.command == "verify":
            verify(a)
        else:
            if not a.db:
                raise SystemExit("restore needs --db NAME (a scratch database, or the live one with --into-live)")
            restore(a)
    return 0


if __name__ == "__main__":
    sys.exit(main())
