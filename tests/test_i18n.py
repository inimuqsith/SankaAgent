"""Language catalog contract: YAML files are the only source of chat-visible strings."""

from __future__ import annotations

import pytest

from sanka.engine import i18n
from sanka.engine.commands import REGISTRY
from sanka.engine.i18n import available, catalog, normalize_language, reload_catalogs, t


def test_catalogs_have_identical_keys() -> None:
    codes = available()
    assert "en" in codes and "id" in codes
    reference = catalog("en")
    assert reference
    assert all(value.strip() for value in reference.values())
    for code in codes:
        body = catalog(code)
        assert set(body) == set(reference), code
        assert all(value.strip() for value in body.values()), code


def test_every_core_command_has_a_description() -> None:
    for code in available():
        body = catalog(code)
        for name in REGISTRY.names():
            assert f"command.{name}.description" in body, (code, name)
        assert "gateway.backfill.description" in body, code


def test_new_catalog_file_is_discovered(tmp_path, monkeypatch) -> None:
    (tmp_path / "xx.yaml").write_text("messages:\n  greeting: Halo dari xx\n", encoding="utf-8")
    monkeypatch.setattr(i18n, "CATALOG_DIR", tmp_path)
    reload_catalogs()
    try:
        assert available() == ("xx",)
        assert normalize_language("xx") == "xx"
        assert t("greeting", "xx") == "Halo dari xx"
    finally:
        reload_catalogs()


def test_unknown_language_lists_available() -> None:
    with pytest.raises(ValueError) as caught:
        normalize_language("jv")
    message = str(caught.value)
    assert "available" in message
    assert "id" in message and "en" in message


def test_missing_key_and_bad_placeholder_never_raise() -> None:
    assert t("nope.missing", "id") == "nope.missing"
    assert t("nope.missing", "id", default="fallback") == "fallback"
    assert t("command.mood.energy", "id") == "• Energi: {energy}"
    assert t("command.mood.energy", "xx", energy="full") == "• Energy: full"
