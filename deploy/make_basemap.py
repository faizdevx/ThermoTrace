"""Build the self-hosted vector basemap: an India extract of the Protomaps OpenStreetMap build, as one PMTiles file.

    python deploy/make_basemap.py                         # data/basemap/india.pmtiles, zoom 0-12, about 690 MB
    python deploy/make_basemap.py --maxzoom 10            # about 130 MB (roads and towns, less street detail)
    python deploy/make_basemap.py --dry-run               # only report the size

This is the only step that needs the internet, and it runs once on a machine that has it; copy the file to the offline site. It uses the
official ``pmtiles`` command line tool from github.com/protomaps/go-pmtiles: when ``pmtiles`` is not on PATH the release for this platform
is downloaded into ``data/tools/pmtiles/`` and its SHA-256 is checked against the digest GitHub publishes for the release asset.
The map data is (c) OpenStreetMap contributors (ODbL); the build and the style come from Protomaps.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import platform
import shutil
import subprocess
import sys
import tarfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "data" / "tools" / "pmtiles"
BUILDS = "https://build-metadata.protomaps.dev/builds.json"
RELEASE = "https://api.github.com/repos/protomaps/go-pmtiles/releases/latest"


def _get(url: str) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "ThermoTrace-basemap/1.0"})
    with urllib.request.urlopen(req, timeout=60) as r:  # noqa: S310
        return r.read()


def find_cli() -> Path:
    found = shutil.which("pmtiles")
    if found:
        return Path(found)
    system, machine = platform.system(), platform.machine().lower()
    arch = "arm64" if machine in ("arm64", "aarch64") else "x86_64"
    osname = {"Windows": "Windows", "Darwin": "Darwin", "Linux": "Linux"}[system]
    rel = json.loads(_get(RELEASE))
    asset = next(a for a in rel["assets"] if f"_{osname}_{arch}" in a["name"] and a["name"].endswith((".zip", ".tar.gz")))
    digest = (asset.get("digest") or "").removeprefix("sha256:")
    if not digest:
        raise SystemExit("GitHub published no checksum for the pmtiles release asset: refusing to run an unverified download")
    TOOLS.mkdir(parents=True, exist_ok=True)
    archive = TOOLS / asset["name"]
    archive.write_bytes(_get(asset["browser_download_url"]))
    got = hashlib.sha256(archive.read_bytes()).hexdigest()
    if got != digest:
        archive.unlink()
        raise SystemExit(f"checksum mismatch for {asset['name']}: expected {digest}, got {got}")
    if archive.suffix == ".zip":
        zipfile.ZipFile(archive).extractall(TOOLS)
    else:
        tarfile.open(archive).extractall(TOOLS, filter="data")
    exe = next(TOOLS.rglob("pmtiles.exe" if system == "Windows" else "pmtiles"))
    exe.chmod(0o755)
    print(f"downloaded pmtiles {rel['tag_name']} (sha256 verified) to {exe}")
    return exe


def latest_build() -> str:
    builds = json.loads(_get(BUILDS))
    return f"https://build.protomaps.com/{builds[-1]['key']}"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "data" / "basemap" / "india.pmtiles"))
    ap.add_argument("--bbox", default="68,6,98,37", help="west,south,east,north (default: India with a margin)")
    ap.add_argument("--maxzoom", type=int, default=12)
    ap.add_argument("--source", help="URL or path of a Protomaps build (default: the newest daily build)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    cli = find_cli()
    source = a.source or latest_build()
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    cmd = [str(cli), "extract", source, a.out, f"--bbox={a.bbox}", f"--maxzoom={a.maxzoom}"] + (["--dry-run"] if a.dry_run else [])
    print(" ".join(cmd))
    return subprocess.run(cmd, check=False).returncode


if __name__ == "__main__":
    sys.exit(main())
