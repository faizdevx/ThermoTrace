"""OpenStreetMap adapter: baseline import + incremental replication via osm2pgsql.

Uses the mature osm2pgsql toolchain (flex output, slim/append mode) and its
``osm2pgsql-replication`` helper; this module never parses OSM data itself.

Source semantics
----------------
* Baseline: a regional Geofabrik extract (ODbL) - not a country-wide Overpass query.
* Increments: the extract's own replication diffs (``<region>-updates``). For
  Geofabrik regional extracts these are published DAILY, so end-to-end freshness is
  bounded by the source cadence, not by this worker. Point ``OSM_REPLICATION_URL`` at
  a faster compatible server to tighten it.
"""
from __future__ import annotations

import hashlib
import logging
import os
import subprocess
import threading
import urllib.parse
from dataclasses import dataclass
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Callable, Optional

import requests

from .config import OsmSourceConfig, SyncConfig

logger = logging.getLogger(__name__)


class SourceUnavailable(RuntimeError):
    """The upstream source could not be reached or returned an unusable answer."""


class ReplicationError(RuntimeError):
    """osm2pgsql / osm2pgsql-replication failed."""


@dataclass(frozen=True)
class RemoteState:
    sequence: int
    timestamp: datetime            # data timestamp of that sequence
    published_at: Optional[datetime]  # HTTP Last-Modified of state.txt (best publication-time evidence)


@dataclass(frozen=True)
class LocalState:
    sequence: int
    timestamp: Optional[datetime]
    base_url: Optional[str]


def parse_state_text(text: str) -> tuple[int, datetime]:
    """Parse an OSM replication ``state.txt``."""
    values: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip().replace("\\:", ":")
    try:
        sequence = int(values["sequenceNumber"])
        timestamp = datetime.fromisoformat(values["timestamp"].replace("Z", "+00:00"))
    except (KeyError, ValueError) as exc:
        raise SourceUnavailable(f"unparseable replication state: {text[:120]!r}") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=timezone.utc)
    return sequence, timestamp


def fetch_remote_state(cfg: SyncConfig, session: requests.Session | None = None) -> RemoteState:
    """Ask the replication server for its newest state (one tiny GET)."""
    http = session or requests.Session()
    url = f"{cfg.osm.replication_url}/state.txt"
    try:
        response = http.get(
            url, timeout=cfg.http_timeout_seconds, headers={"User-Agent": cfg.user_agent}
        )
        response.raise_for_status()
    except requests.RequestException as exc:
        raise SourceUnavailable(f"cannot read {url}: {exc}") from exc
    sequence, timestamp = parse_state_text(response.text)
    published_at = None
    last_modified = response.headers.get("Last-Modified")
    if last_modified:
        try:
            published_at = parsedate_to_datetime(last_modified)
        except (TypeError, ValueError):
            published_at = None
    return RemoteState(sequence, timestamp, published_at)


def read_local_state(cur) -> Optional[LocalState]:
    """Replication state recorded by osm2pgsql in ``osm2pgsql_properties`` (None before import)."""
    cur.execute("SELECT to_regclass('public.osm2pgsql_properties') IS NOT NULL")
    if not cur.fetchone()[0]:
        return None
    cur.execute(
        "SELECT property, value FROM osm2pgsql_properties WHERE property LIKE 'replication_%'"
    )
    props = dict(cur.fetchall())
    if "replication_sequence_number" not in props:
        return None
    timestamp = None
    raw_ts = props.get("replication_timestamp")
    if raw_ts:
        timestamp = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
        if timestamp.tzinfo is None:
            timestamp = timestamp.replace(tzinfo=timezone.utc)
    return LocalState(int(props["replication_sequence_number"]), timestamp, props.get("replication_base_url"))


def pg_env(dsn: str) -> dict[str, str]:
    """Environment for libpq tools so passwords never appear in process arguments."""
    parsed = urllib.parse.urlparse(dsn)
    env = dict(os.environ)
    if parsed.hostname:
        env["PGHOST"] = parsed.hostname
    if parsed.port:
        env["PGPORT"] = str(parsed.port)
    if parsed.username:
        env["PGUSER"] = urllib.parse.unquote(parsed.username)
    if parsed.password:
        env["PGPASSWORD"] = urllib.parse.unquote(parsed.password)
    if parsed.path and len(parsed.path) > 1:
        env["PGDATABASE"] = parsed.path.lstrip("/")
    return env


def _run(cmd: list[str], env: dict[str, str], timeout: int, stop: threading.Event | None,
         log: Callable[[str], None] = logger.info) -> str:
    """Run a subprocess with timeout, honouring a stop event; returns combined output tail."""
    logger.info("exec: %s", " ".join(cmd))
    proc = subprocess.Popen(cmd, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    tail: list[str] = []

    def pump() -> None:
        assert proc.stdout is not None
        for line in proc.stdout:
            line = line.rstrip()
            tail.append(line)
            del tail[:-60]
            log(line)

    reader = threading.Thread(target=pump, daemon=True)
    reader.start()
    waited = 0.0
    while proc.poll() is None:
        if stop is not None and stop.is_set():
            proc.terminate()
            try:
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
            raise ReplicationError("interrupted by shutdown request")
        if waited > timeout:
            proc.kill()
            raise ReplicationError(f"timed out after {timeout}s: {' '.join(cmd[:2])}")
        try:
            proc.wait(timeout=1)
        except subprocess.TimeoutExpired:
            waited += 1
    reader.join(timeout=5)
    output = "\n".join(tail)
    if proc.returncode != 0:
        raise ReplicationError(f"{cmd[0]} exited {proc.returncode}: {output[-600:]}")
    return output


def run_replication_update(cfg: SyncConfig, stop: threading.Event | None = None) -> str:
    """Apply all newly available replication diffs (osm2pgsql append mode).

    osm2pgsql commits its own transactions and advances ``osm2pgsql_properties``
    atomically per batch, so an interruption never leaves the raw table half-updated.
    """
    osm = cfg.osm
    cmd = [
        "osm2pgsql-replication", "update", "-d", "dbname=" + _dbname(cfg.dsn),
        "--max-diff-size", str(osm.max_diff_size_mb),
        "--",
        "--cache", str(osm.cache_size_mb),
        "--number-processes", "2",
    ]
    return _run(cmd, pg_env(cfg.dsn), osm.update_timeout_seconds, stop)


def _dbname(dsn: str) -> str:
    parsed = urllib.parse.urlparse(dsn)
    return parsed.path.lstrip("/") or os.getenv("PGDATABASE", "")


def download_extract(cfg: SyncConfig, dest_dir: Path, force: bool = False) -> Path:
    """Download the baseline extract (resumable) and verify it against Geofabrik's .md5."""
    osm = cfg.osm
    dest_dir.mkdir(parents=True, exist_ok=True)
    target = dest_dir / "extract.osm.pbf"
    marker = dest_dir / "extract.source"
    headers = {"User-Agent": cfg.user_agent}
    expected_md5 = None
    try:
        md5_resp = requests.get(osm.extract_url + ".md5", headers=headers, timeout=cfg.http_timeout_seconds)
        if md5_resp.ok and md5_resp.text.strip():
            expected_md5 = md5_resp.text.split()[0]
    except requests.RequestException:
        logger.warning("could not fetch %s.md5 - checksum verification skipped", osm.extract_url)

    if target.exists() and not force and marker.exists() and marker.read_text().strip() == osm.extract_url:
        if expected_md5 is None or _md5(target) == expected_md5:
            logger.info("extract already present and verified: %s", target)
            return target
    temp = target.with_suffix(".part")
    resume = temp.stat().st_size if temp.exists() else 0
    req_headers = dict(headers)
    if resume:
        req_headers["Range"] = f"bytes={resume}-"
    logger.info("downloading %s (resume at %d bytes)", osm.extract_url, resume)
    try:
        with requests.get(osm.extract_url, headers=req_headers, stream=True,
                          timeout=(10, 120), allow_redirects=True) as response:
            response.raise_for_status()
            mode = "ab" if resume and response.status_code == 206 else "wb"
            with open(temp, mode) as handle:
                for chunk in response.iter_content(chunk_size=1 << 20):
                    handle.write(chunk)
    except requests.RequestException as exc:
        raise SourceUnavailable(f"extract download failed: {exc}") from exc
    if expected_md5 is not None and _md5(temp) != expected_md5:
        temp.unlink(missing_ok=True)
        raise SourceUnavailable("extract checksum mismatch; deleted partial file")
    temp.replace(target)
    marker.write_text(osm.extract_url)
    return target


def _md5(path: Path) -> str:
    digest = hashlib.md5()  # noqa: S324 - integrity check against the publisher's .md5, not security
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def import_baseline(cfg: SyncConfig, pbf: Path, stop: threading.Event | None = None) -> None:
    """osm2pgsql --create (slim, flex) then ``osm2pgsql-replication init`` for the same server.

    Node locations are kept in the database by default. A flat-nodes file is opt-in
    (``OSM_FLAT_NODES_PATH``): its apparent size scales with the highest OSM node id (~100 GB
    sparse for any planet-derived extract), which is unsuitable for small disks.
    """
    osm = cfg.osm
    env = pg_env(cfg.dsn)
    command = [
        "osm2pgsql", "-d", _dbname(cfg.dsn), "--create", "--slim",
        "--output=flex", f"--style={osm.style_path}", "--extra-attributes",
        "--cache", str(osm.cache_size_mb), "--number-processes", "2",
    ]
    if osm.flat_nodes_path:
        Path(osm.flat_nodes_path).parent.mkdir(parents=True, exist_ok=True)
        Path(osm.flat_nodes_path).unlink(missing_ok=True)   # file from an interrupted import is corrupt
        command.append(f"--flat-nodes={osm.flat_nodes_path}")
    _run(command + [str(pbf)], env, 6 * 3600, stop)
    _run(
        ["osm2pgsql-replication", "init", "-d", _dbname(cfg.dsn), "--server", osm.replication_url],
        env, 600, stop,
    )
