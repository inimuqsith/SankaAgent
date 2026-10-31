#!/usr/bin/env python3
"""Alat katalog fakta: validasi, hitung vektor, dan statistik.

Fakta = isi yang boleh diketahui Sanka (bukan cara bicara; itu `patterns.json`).
Setiap fakta wajib punya minimal satu URL sumber, dan `confidence` 3 bila dua domain atau
lebih mendukungnya.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
DATA = ROOT / "sanka" / "data" / "fakta.json"
VECTORS = ROOT / "sanka" / "data" / "fakta_vectors.json"
DIM = 384
TEXT_MIN, TEXT_MAX = 10, 220
KEY_MAX, KEY_WORDS = 4, 4
MIN_DOMAINS = 1
ID = re.compile(r"^[a-z0-9]+(?:[-_][a-z0-9]+)*$")
TOPIC = re.compile(r"^T\d+$")


def load(path: Path | str = DATA) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def rows(body: dict) -> list[dict]:
    found = body.get("facts") if isinstance(body, dict) else None
    return [row for row in found or [] if isinstance(row, dict)]


def _id_deficits(where: str, row: dict, seen: set[str]) -> list[str]:
    ident = row.get("id")
    if not isinstance(ident, str) or not ID.match(ident):
        return [f"{where}: id harus slug huruf kecil, dapat {ident!r}"]
    if ident in seen:
        return [f"{where}: id ganda {ident}"]
    seen.add(ident)
    return []


def _text_deficits(where: str, row: dict) -> list[str]:
    text = row.get("text")
    if not isinstance(text, str) or not TEXT_MIN <= len(text) <= TEXT_MAX:
        return [f"{where}: text harus {TEXT_MIN}-{TEXT_MAX} karakter"]
    if "\n" in text:
        return [f"{where}: text satu baris"]
    if text[:1].isalpha() and text[:1].islower():
        return [f"{where}: text harus diawali huruf besar"]
    return []


def _key_deficits(where: str, row: dict) -> list[str]:
    found = row.get("keys")
    if not isinstance(found, list) or not found:
        return [f"{where}: keys harus daftar tidak kosong"]
    if len(found) > KEY_MAX:
        return [f"{where}: keys maksimal {KEY_MAX}"]
    out: list[str] = []
    seen: set[str] = set()
    for key in found:
        if not isinstance(key, str) or not key.strip() or key != key.lower():
            out.append(f"{where}: key {key!r} harus huruf kecil")
            continue
        if not 1 <= len(key.split()) <= KEY_WORDS:
            out.append(f"{where}: key {key!r} harus 1-{KEY_WORDS} kata")
        if key in seen:
            out.append(f"{where}: key ganda {key!r}")
        seen.add(key)
    return out


def _source_deficits(where: str, row: dict) -> list[str]:
    out: list[str] = []
    sources = row.get("sources")
    if not isinstance(sources, list) or not sources:
        out.append(f"{where}: sources harus daftar tidak kosong")
    elif any(not isinstance(url, str) or not url.startswith("http") for url in sources):
        out.append(f"{where}: setiap sumber harus URL http")
    domains = row.get("domains")
    if not isinstance(domains, list) or len(domains) < MIN_DOMAINS:
        out.append(f"{where}: domains harus >= {MIN_DOMAINS} (bukti lintas sumber)")
    if not isinstance(row.get("topic"), str) or not TOPIC.match(str(row.get("topic"))):
        out.append(f"{where}: topic harus id riset seperti T21")
    confidence = row.get("confidence")
    if not isinstance(confidence, int) or isinstance(confidence, bool) or not 1 <= confidence <= 3:
        out.append(f"{where}: confidence harus int 1..3")
    return out


def validate(body: dict) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    found = rows(body)
    if not found:
        out.append("tidak ada fakta")
    for index, row in enumerate(found, start=1):
        where = f"fakta[{index}] {row.get('id') or '-'}"
        out += _id_deficits(where, row, seen)
        out += _text_deficits(where, row)
        out += _key_deficits(where, row)
        out += _source_deficits(where, row)
    return out


def vectors_deficits(body: dict, path: Path) -> list[str]:
    if not path.is_file():
        return [f"{path.name}: berkas vektor tidak ada"]
    try:
        stored = json.loads(path.read_text(encoding="utf-8"))
    except ValueError:
        return [f"{path.name}: JSON tidak valid"]
    if str(stored.get("model") or "") != MODEL:
        return [f"{path.name}: model tidak cocok"]
    if int(stored.get("dim") or 0) != DIM:
        return [f"{path.name}: dim harus {DIM}"]
    vectors = stored.get("vectors") if isinstance(stored.get("vectors"), dict) else {}
    missing = [row["id"] for row in rows(body) if not isinstance(vectors.get(row.get("id")), list)]
    return [f"{path.name}: vektor hilang untuk {', '.join(missing)}"] if missing else []


def _atomic_write(path: Path, payload: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp = tempfile.mkstemp(dir=str(path.parent), prefix=".fakta-", suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(payload)
        os.replace(temp, path)
    except BaseException:
        Path(temp).unlink(missing_ok=True)
        raise


def _encoder(model: str):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(model, device="cpu")


def embed(body: dict, path: Path = VECTORS) -> dict:
    model = str(body.get("model") or MODEL)
    found = rows(body)
    if not found:
        raise SystemExit("tidak ada fakta untuk di-embed")
    encoder = _encoder(model)
    vectors = encoder.encode(
        [row["text"] for row in found], batch_size=32, normalize_embeddings=True
    )
    built = {
        "version": int(body.get("version") or 1),
        "model": model,
        "dim": int(len(vectors[0])),
        "vectors": {
            row["id"]: [round(float(value), 6) for value in vector]
            for row, vector in zip(found, vectors, strict=True)
        },
    }
    _atomic_write(path, json.dumps(built, ensure_ascii=False) + "\n")
    return built


def _stats(body: dict) -> list[str]:
    found = rows(body)
    topics: dict[str, int] = {}
    for row in found:
        topics[row["topic"]] = topics.get(row["topic"], 0) + 1
    top = ", ".join(
        f"{name}={count}" for name, count in sorted(topics.items(), key=lambda pair: -pair[1])
    )
    keys = sum(len(row.get("keys") or []) for row in found)
    domains = sum(len(row.get("domains") or []) for row in found)
    return [
        f"fakta={len(found)} version={body.get('version')} model={body.get('model')}",
        f"topic: {top}",
        f"keys={keys} rata-rata={keys / len(found):.1f} | domain={domains}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Katalog fakta Sanka")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true", help="validasi skema + vektor")
    group.add_argument("--embed", action="store_true", help="hitung ulang vektor")
    group.add_argument("--stats", action="store_true", help="ringkasan katalog")
    args = parser.parse_args(argv)

    body = load()
    if args.embed:
        built = embed(body)
        print(f"vectors={len(built['vectors'])} model={built['model']} dim={built['dim']}")
        return 0
    if args.stats:
        print("\n".join(_stats(body)))
        return 0
    problems = validate(body) + vectors_deficits(body, VECTORS)
    if problems:
        print("\n".join(problems))
        return 1
    print(f"fakta={len(rows(body))} ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
