#!/usr/bin/env python3
"""Pasang skill opsional SentiCore (AGPL) ke folder skill runtime, bukan ke repo.

Core Sanka tidak butuh ini: SOUL.md + emotion-engine sudah cukup. Script ini hanya
menyalin `emotion_skill_en.md` sebagai skill lokal di `.sanka/skills/senticore` supaya
model bisa memuat kosakata emosi 30 dimensi bila perlu. Isi SentiCore tidak di-commit.

Pemakaian: python scripts/install_engines.py [--dir .sanka/skills]
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = "https://github.com/chuchuyei/SentiCore"
SKILL_DIR = "senticore"
SOURCE = "emotion_skill_en.md"
SKILL = """---
name: senticore
description: 30-dimensional emotion vocabulary and continuity guide
---

# SentiCore

Panduan kosakata emosi 30 dimensi. Muat `emotion_skill_en.md` di folder skill ini
(read file="emotion_skill_en.md") saat butuh nama rasa yang lebih presisi daripada
PAD saja. Skill ini hanya referensi bahasa; keputusan dan state tetap milik Sanka.
"""


def install(target: Path) -> bool:
    skill = target / SKILL_DIR
    if (skill / "SKILL.md").is_file():
        print(f"senticore: sudah ada di {skill}")
        return True
    with tempfile.TemporaryDirectory() as tmp:
        clone = Path(tmp) / "senticore"
        try:
            subprocess.run(
                ["git", "clone", "--depth", "1", REPO, str(clone)],
                check=True,
                capture_output=True,
                text=True,
            )
        except (OSError, subprocess.CalledProcessError) as exc:
            print(f"senticore: clone gagal, skill dilewati ({exc})", file=sys.stderr)
            return False
        source = next(clone.rglob(SOURCE), None)
        if source is None:
            print(f"senticore: {SOURCE} tidak ada di repo, skill dilewati", file=sys.stderr)
            return False
        skill.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, skill / SOURCE)
    (skill / "SKILL.md").write_text(SKILL, encoding="utf-8")
    print(f"senticore: terpasang di {skill}")
    return True


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dir", default=".sanka/skills", help="folder skill runtime")
    args = parser.parse_args(argv)
    target = Path(args.dir).expanduser()
    target.mkdir(parents=True, exist_ok=True)
    return 0 if install(target) else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
