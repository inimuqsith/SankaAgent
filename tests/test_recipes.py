"""Kontrak resep user: store persisten di database, validasi, eksekutor rantai tool."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sanka.engine.config import Config
from sanka.engine.registry import Handler
from sanka.engine.schema import recipes as recipes_table
from sanka.engine.stores.recipes import RecipeStore, run_recipe
from sanka.engine.tools import ToolContext
from support import build_config, database, inbound_message, make_agent, role


def make_config(root: Path) -> Config:
    return build_config(
        root,
        roles={"owner": role("owner")},
        default_role="owner",
        workspace=root / "workspace",
    )


def echo_specs() -> dict:
    async def echo(args: dict, ctx: ToolContext) -> str:
        return f"got:{args.get('q')}"

    return {"echo": Handler(name="echo", description="e", fn=echo)}


def test_store_round_trip_and_persistence(tmp_path: Path) -> None:
    db = database(tmp_path)
    store = RecipeStore(db)
    assert (
        store.save(
            "rekap",
            "recipe",
            "Rekap mingguan.",
            usage="[divisi]",
            steps=[{"tool": "echo", "args": {"q": "x {{args}}"}}],
        )
        == "rekap"
    )

    recipe = store.get("rekap")
    assert recipe is not None and recipe.kind == "recipe"
    assert recipe.roles == ("owner", "member")
    assert recipe.steps[0]["args"] == {"q": "x {{args}}"}
    assert "- /rekap [divisi] — Rekap mingguan." in store.index()

    reopened = RecipeStore(db)
    assert reopened.get("rekap") is not None

    store.remove("rekap")
    assert store.names() == []
    with pytest.raises(KeyError):
        store.remove("rekap")


def test_store_validation_rejects_bad_definitions(tmp_path: Path) -> None:
    store = RecipeStore(database(tmp_path))
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


def test_store_duplicate_and_overwrite(tmp_path: Path) -> None:
    store = RecipeStore(database(tmp_path))
    store.save("prompt", "prompt", "Versi satu.", prompt="lakukan")
    with pytest.raises(FileExistsError):
        store.save("prompt", "prompt", "Versi dua.", prompt="lakukan lagi")
    store.save("prompt", "prompt", "Versi dua.", prompt="lakukan lagi", overwrite=True)
    recipe = store.get("prompt")
    assert recipe is not None and recipe.description == "Versi dua."
    assert recipe.kind == "prompt" and recipe.prompt == "lakukan lagi"


def test_store_skips_corrupt_rows(tmp_path: Path) -> None:
    db = database(tmp_path)
    with db.connect() as conn:
        db.upsert(
            conn,
            recipes_table,
            [
                {"name": "rusak", "body": "name: [unclosed", "updated_at": 0.0},
                {
                    "name": "salah",
                    "body": "name: beda\nkind: prompt\ndescription: d\nprompt: p\n",
                    "updated_at": 0.0,
                },
            ],
            ["name"],
        )
    store = RecipeStore(db)

    assert store.names() == []
    assert store.get("rusak") is None


def test_run_recipe_chain_substitution_and_failure(tmp_path: Path) -> None:
    store = RecipeStore(database(tmp_path))
    config = make_config(tmp_path)
    ctx = ToolContext(config, config.roles.default_role(), ("*",))
    specs = echo_specs()

    store.save(
        "ok",
        "recipe",
        "d",
        steps=[
            {"tool": "echo", "args": {"q": "a {{args}} b"}},
            {"tool": "echo", "args": {"q": "last"}},
        ],
    )
    assert asyncio.run(run_recipe(store.get("ok"), "DIV", ctx, specs)) == "got:last"

    store.save("satu", "recipe", "d", steps=[{"tool": "echo", "args": {"q": "[{{args}}]"}}])
    assert asyncio.run(run_recipe(store.get("satu"), "x", ctx, specs)) == "got:[x]"

    store.save("gagal", "recipe", "d", steps=[{"tool": "nosuch", "args": {}}])
    out = asyncio.run(run_recipe(store.get("gagal"), "", ctx, specs))
    assert out.startswith("error: langkah 1 (nosuch) gagal:")


def test_agent_dispatches_recipe_command(tmp_path: Path) -> None:
    env = make_agent(tmp_path)
    (env.config.workspace / "public" / "catatan.txt").write_text(
        "halo dari resep", encoding="utf-8"
    )
    env.recipes.save(
        "baca",
        "recipe",
        "Baca catatan.",
        steps=[{"tool": "read", "args": {"path": "public/catatan.txt"}}],
    )

    asyncio.run(env.agent.handle(inbound_message("/baca", chat_id="chat"), env.gateway))

    assert env.gateway.sent
    assert "halo dari resep" in env.gateway.sent[0][1]
