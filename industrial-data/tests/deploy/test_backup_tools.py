"""Backup tooling that needs no database: checksum, manifest, retention and the refusals (the docker-backed paths were run against a real stack)."""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location("pg_backup", Path(__file__).resolve().parents[3] / "deploy" / "pg_backup.py")
pg_backup = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(pg_backup)


def _make(tmp_path, name, payload=b"dump-bytes"):
    f = tmp_path / name
    f.write_bytes(payload)
    man = {"sha256": pg_backup.sha256_file(f), "encrypted": False, "row_counts": {"alerts": 3}}
    f.with_name(f.name + ".manifest.json").write_text(json.dumps(man))
    return f, man


def test_checksum_catches_a_changed_file(tmp_path):
    f, man = _make(tmp_path, "thermotrace-db-20261016T000000Z.dump")
    pg_backup.check_file(f, man)
    f.write_bytes(b"dump-bytez")
    with pytest.raises(SystemExit, match="CHECKSUM MISMATCH"):
        pg_backup.check_file(f, man)


def test_restore_refuses_a_backup_without_a_manifest(tmp_path):
    f = tmp_path / "x.dump"
    f.write_bytes(b"x")
    with pytest.raises(SystemExit, match="no manifest"):
        pg_backup.load_manifest(f)


def test_retention_keeps_the_newest_n_and_their_manifests(tmp_path):
    names = [f"thermotrace-db-2026101{d}T000000Z.dump" for d in range(1, 6)]
    for n in names:
        _make(tmp_path, n)
    _make(tmp_path, "thermotrace-other-20261001T000000Z.dump")           # another database is not pruned
    pg_backup.prune(tmp_path, 2, "db")
    left = sorted(p.name for p in tmp_path.glob("thermotrace-db-*.dump"))
    assert left == names[-2:]
    assert not (tmp_path / (names[0] + ".manifest.json")).exists() and (tmp_path / "thermotrace-other-20261001T000000Z.dump").exists()


def test_encrypted_backups_need_a_passphrase_variable(tmp_path, monkeypatch):
    f, man = _make(tmp_path, "a.dump.enc")
    man["encrypted"] = "aes-256-cbc-pbkdf2"
    with pytest.raises(SystemExit, match="encrypted"):
        pg_backup.plain_dump(f, man, None, tmp_path)
    monkeypatch.delenv("NO_SUCH_VAR", raising=False)
    with pytest.raises(SystemExit, match="empty"):
        pg_backup.openssl("dec", f, tmp_path / "out", "NO_SUCH_VAR")
