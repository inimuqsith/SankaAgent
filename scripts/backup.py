#!/usr/bin/env python3
"""Arsip data runtime SankaAgent: SQLite konsisten, workspace, config, dan env.

Jalankan dari mana saja: `python scripts/backup.py`.
DB disalin lewat sqlite backup API, jadi aman saat bot masih hidup. Untuk cutover,
hentikan bot dulu lalu jalankan ulang agar snapshot benar-benar final.
"""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DBS = ("whatsapp.db", "history.db", "memory.db")


def snapshot(src: Path, dst: Path) -> None:
    with sqlite3.connect(src, timeout=30) as source, sqlite3.connect(dst) as target:
        source.backup(target)
    dst.chmod(0o600)
    with sqlite3.connect(dst) as check:
        state = check.execute("PRAGMA integrity_check").fetchone()[0]
        if state != "ok":
            raise SystemExit(f"backup rusak: {src} ({state})")
        busy, _, _ = check.execute("PRAGMA wal_checkpoint(TRUNCATE)").fetchone()
        if busy:
            raise SystemExit(f"backup sibuk, checkpoint gagal: {src}")
        check.execute("PRAGMA journal_mode=DELETE")
    for suffix in ("-wal", "-shm"):
        Path(f"{dst}{suffix}").unlink(missing_ok=True)


def git(*args: str) -> str:
    try:
        result = subprocess.run(
            ["git", *args], cwd=ROOT, check=True, capture_output=True, text=True
        )
    except (OSError, subprocess.CalledProcessError):
        return "?"
    return result.stdout.strip()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def stage(data: Path, config: Path, env: Path, staging: Path) -> list[str]:
    copied: list[str] = []
    out = staging / ".sanka"
    out.mkdir()
    for name in DBS:
        src = data / name
        if src.is_file():
            snapshot(src, out / name)
            copied.append(f".sanka/{name} ({sha256(out / name)[:16]})")
    for name in ("skills", "workspace"):
        src = data / name
        if src.is_dir():
            shutil.copytree(src, out / name, dirs_exist_ok=True)
            copied.append(f".sanka/{name}/")
    for src, name in ((config, "config.yaml"), (env, ".env")):
        if src.is_file():
            shutil.copy2(src, staging / name)
            (staging / name).chmod(0o600)
            copied.append(f"{name} ({sha256(staging / name)[:16]})")
    return copied


def manifest(data: Path, copied: list[str], created: str) -> str:
    commit = git("rev-parse", "--short", "HEAD")
    status = git("status", "--porcelain")
    lines = [
        "SankaAgent backup",
        f"created: {created}",
        f"source: {ROOT}",
        f"commit: {commit}{' (dirty)' if status else ''}",
        "",
        "isi:",
        *(f"- {item}" for item in copied),
        "",
        "restore:",
        "1. Hentikan bot supaya tidak ada dua proses memakai session yang sama.",
        "2. Taruh isi .sanka/ di folder data project; config.yaml dan .env di root project.",
        "3. chown -R 1000:1000 <project> (container) dan chmod 600 .env .sanka/*.db.",
        "4. Jalankan bot; tidak perlu scan QR bila .sanka/whatsapp.db utuh.",
        "",
        "catatan: config.yaml berisi nomor owner dan .env berisi API key; jangan di-commit.",
        "log sanka.log tidak diarsipkan.",
    ]
    return "\n".join(lines) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(description="Arsip data runtime SankaAgent.")
    parser.add_argument("--data", type=Path, default=ROOT / ".sanka", help="folder data (default .sanka)")
    parser.add_argument("--config", type=Path, default=ROOT / "config.yaml")
    parser.add_argument("--env", type=Path, default=ROOT / ".env")
    parser.add_argument("--out", type=Path, default=Path.home() / "backups")
    args = parser.parse_args()

    if not args.data.is_dir():
        raise SystemExit(f"folder data tidak ditemukan: {args.data}")
    args.out.mkdir(parents=True, exist_ok=True)
    created = datetime.now().astimezone().isoformat(timespec="seconds")
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    commit = git("rev-parse", "--short", "HEAD")
    archive = args.out / f"sanka-{stamp}-{commit}.tar.gz"

    with tempfile.TemporaryDirectory(prefix="sanka-backup-") as tmp:
        staging = Path(tmp)
        copied = stage(args.data.resolve(), args.config, args.env, staging)
        (staging / "MANIFEST.txt").write_text(manifest(args.data, copied, created), encoding="utf-8")
        with tarfile.open(archive, "w:gz") as tar:
            for child in sorted(staging.iterdir()):
                tar.add(child, arcname=child.name, recursive=True)
    archive.chmod(0o600)

    size = archive.stat().st_size / (1024 * 1024)
    print(f"arsip  : {archive}")
    print(f"ukuran : {size:.1f} MiB")
    print(f"sha256 : {sha256(archive)}")
    print("catatan: snapshot DB konsisten; untuk cutover hentikan bot lalu ulangi.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
