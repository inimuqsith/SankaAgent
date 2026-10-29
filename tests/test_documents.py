"""Document mirror contract: seed from files, export/import, documents win, roles aggregate."""

from __future__ import annotations

import yaml

from sanka.engine.stores.documents import DocumentStore
from support import build_config, database


def make_docs(tmp_path, *, sync=True):
    config = build_config(tmp_path)
    return DocumentStore(database(tmp_path), config.paths, sync=sync), config.paths


def write_config(paths, body: dict) -> None:
    paths.config.write_text(yaml.safe_dump(body, sort_keys=False), encoding="utf-8")


def read_config(paths) -> dict:
    return yaml.safe_load(paths.config.read_text(encoding="utf-8"))


def test_seed_only_missing_documents(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(
        paths,
        {
            "workspace": ".sanka/workspace",
            "engine": {"default_role": "guest"},
            "soul": {"name": "sanka", "language": "id"},
        },
    )
    paths.soul_md.write_text("Kamu Sanka.", encoding="utf-8")
    paths.roles.write_text("roles:\n  guest:\n    name: guest\n", encoding="utf-8")

    assert docs.seed() == 4
    assert "default_role: guest" in docs.get("engine")
    assert docs.names("role:") == ["role:guest"]
    assert docs.seed() == 0

    docs.put("soul", "name: sanka\nlanguage: en\n")
    assert docs.seed() == 0
    assert "language: en" in docs.get("soul")


def test_export_and_import_roundtrip(tmp_path) -> None:
    docs, paths = make_docs(tmp_path)
    write_config(paths, {"workspace": ".sanka/workspace", "gateways": {"cli": {"enabled": True}}})
    docs.put("engine", "default_role: guest\n")
    docs.put("soul", "name: sanka\nlanguage: en\n")
    docs.put("persona", "Persona baru.\n")
    docs.put("role:guest", "name: guest\ntools: []\n")

    assert docs.export_to_files() == 3

    raw = read_config(paths)
    assert raw["workspace"] == ".sanka/workspace"
    assert raw["gateways"] == {"cli": {"enabled": True}}
    assert raw["engine"] == {"default_role": "guest"}
    assert raw["soul"] == {"name": "sanka", "language": "en"}
    assert paths.soul_md.read_text(encoding="utf-8") == "Persona baru.\n"
    roles = yaml.safe_load(paths.roles.read_text(encoding="utf-8"))
    assert roles["roles"]["guest"] == {"name": "guest", "tools": []}

    write_config(
        paths,
        {"workspace": ".sanka/workspace", "engine": {"default_role": "owner"}},
    )
    docs.put("engine", "default_role: guest\n")
    assert docs.import_from_files() == 3
    assert "default_role: owner" in docs.get("engine")


def test_sync_false_never_touches_files(tmp_path) -> None:
    docs, paths = make_docs(tmp_path, sync=False)
    write_config(paths, {"workspace": ".sanka/workspace", "engine": {"default_role": "guest"}})
    assert docs.seed() == 0
    assert docs.get("engine") is None
    docs.put("engine", "x")
    assert docs.export_to_files() == 0
    assert read_config(paths)["engine"] == {"default_role": "guest"}
