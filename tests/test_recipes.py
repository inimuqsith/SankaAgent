"""Kontrak resep user: store persisten, validasi, eksekutor rantai tool."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sanka.config import Config, LLMConfig, RoleConfig, SandboxConfig, WhatsAppConfig
from sanka.recipes import RecipeStore, run_recipe
from sanka.registry import Handler
from sanka.tools import ToolContext


def make_config(root: Path) -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=root / "workspace",
        whatsapp=WhatsAppConfig(session_db=root / "wa.db"),
        roles={"owner": RoleConfig("owner")},
        default_role="owner",
        sandbox=SandboxConfig(),
        mcp={},
    )


def echo_specs() -> dict:
    async def echo(args: dict, ctx: ToolContext) -> str:
        return f"got:{args.get('q')}"

    return {"echo": Handler(name="echo", description="e", fn=echo, defaults={"owner": True})}


def test_store_round_trip_and_persistence(tmp_path: Path) -> None:
    root = tmp_path / "recipes"
    store = RecipeStore(root)
    target = store.save(
        "rekap",
        "recipe",
        "Rekap mingguan.",
        usage="[divisi]",
        steps=[{"tool": "echo", "args": {"q": "x {{args}}"}}],
    )

    assert target == root / "rekap.yaml"
    assert target.is_file()
    recipe = store.get("rekap")
    assert recipe is not None and recipe.kind == "recipe"
    assert recipe.roles == ("owner", "member")
    assert recipe.steps[0]["args"] == {"q": "x {{args}}"}
    assert "- /rekap [divisi] — Rekap mingguan." in store.index()

    reopened = RecipeStore(root)
    assert reopened.get("rekap") is not None

    store.remove("rekap")
    assert store.names() == []
    assert not target.exists()
    with pytest.raises(KeyError):
        store.remove("rekap")


def test_store_validation_rejects_bad_definitions(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path / "recipes")
    good = [{"tool": "echo", "args": {}}]
    with pytest.raises(ValueError):
        store.save("Bad Name!", "recipe", "d", steps=good)
    with pytest.raises(ValueError):
        store.save("ok", "sihir", "d", steps=good)
    with pytest.raises(ValueError):
        store.save("ok", "recipe", "d", steps=[])
    with pytest.raises(ValueError):
        store.save("ok", "prompt", "d", prompt="")
    with pytest.raises(ValueError):
        store.save("help", "prompt", "d", prompt="p")
    with pytest.raises(ValueError):
        store.save("ok", "recipe", "d", steps=[{"tool": "echo", "args": {}}] * 11)
    with pytest.raises(ValueError):
        store.save("ok", "recipe", "", steps=good)


def test_store_skips_corrupt_files(tmp_path: Path) -> None:
    root = tmp_path / "recipes"
    root.mkdir()
    (root / "rusak.yaml").write_text("name: [unclosed", encoding="utf-8")
    (root / "salah.yaml").write_text(
        "name: beda\nkind: prompt\ndescription: d\nprompt: p\n", encoding="utf-8"
    )
    store = RecipeStore(root)

    assert store.names() == []


def test_run_recipe_chain_substitution_and_failure(tmp_path: Path) -> None:
    store = RecipeStore(tmp_path / "recipes")
    config = make_config(tmp_path)
    ctx = ToolContext(config, config.roles["owner"], ("*",))
    specs = echo_specs()

    store.save("ok", "recipe", "d", steps=[
        {"tool": "echo", "args": {"q": "a {{args}} b"}},
        {"tool": "echo", "args": {"q": "last"}},
    ])
    assert asyncio.run(run_recipe(store.get("ok"), "DIV", ctx, specs)) == "got:last"

    store.save("satu", "recipe", "d", steps=[{"tool": "echo", "args": {"q": "[{{args}}]"}}])
    assert asyncio.run(run_recipe(store.get("satu"), "x", ctx, specs)) == "got:[x]"

    store.save("gagal", "recipe", "d", steps=[{"tool": "nosuch", "args": {}}])
    out = asyncio.run(run_recipe(store.get("gagal"), "", ctx, specs))
    assert out.startswith("error: langkah 1 (nosuch) gagal:")
