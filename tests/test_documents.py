"""Kontrak mirror dokumen: seed dari file, export/import, dokumen menang."""

from __future__ import annotations

from sanka.engine.stores.documents import DocumentStore
from support import build_config, database


def make_docs(tmp_path, *, sync=True):
    config = build_config(tmp_path)
    paths = config.paths
    paths.roles.mkdir(parents=True, exist_ok=True)
    return DocumentStore(database(tmp_path), paths, sync=sync), paths


def test_seed_only_missing_documents(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    paths.engine.write_text("default_role: guest\n", encoding="utf-8")
    paths.soul.write_text("name: sanka\nlanguage: id\n", encoding="utf-8")
    paths.soul_md.write_text("Kamu Sanka.", encoding="utf-8")
    (paths.roles / "guest.yaml").write_text("name: guest\n", encoding="utf-8")

    assert docs.seed() == 4
    assert docs.get("engine") == "default_role: guest\n"
    assert docs.names("role:") == ["role:guest"]
    assert docs.seed() == 0

    docs.put("soul", "name: sanka\nlanguage: en\n")
    assert docs.seed() == 0
    assert "language: en" in docs.get("soul")


def test_export_and_import_roundtrip(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    docs.put("engine", "default_role: guest\n")
    docs.put("persona", "Persona baru.\n")
    docs.put("role:guest", "name: guest\ntools: []\n")

    assert docs.export_to_files() == 3
    assert paths.engine.read_text(encoding="utf-8") == "default_role: guest\n"
    assert paths.soul_md.read_text(encoding="utf-8") == "Persona baru.\n"
    assert (paths.roles / "guest.yaml").is_file()

    paths.engine.write_text("default_role: owner\n", encoding="utf-8")
    docs.put("engine", "default_role: guest\n")
    assert docs.import_from_files() == 3
    assert docs.get("engine") == "default_role: owner\n"


def test_sync_false_never_touches_files(tmp_path) -> None:
    docs, paths = make_docs(tmp_path, sync=False)
    paths.engine.write_text("default_role: guest\n", encoding="utf-8")
    assert docs.seed() == 0
    assert docs.get("engine") is None
    docs.put("engine", "x")
    assert docs.export_to_files() == 0
    assert paths.engine.read_text(encoding="utf-8") == "default_role: guest\n"
