"""Kontrak skill: frontmatter, indeks, progressive disclosure, file pendukung, dan save."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sanka.engine.commands import CommandContext
from sanka.engine.commands.skill import COMMAND as SKILLCMD
from sanka.engine.config import Config
from sanka.engine.db import Database
from sanka.engine.schema import skill_files
from sanka.engine.stores.skills import SkillStore, parse_frontmatter
from sanka.engine.tools import REGISTRY as TOOLS
from sanka.engine.tools import ToolContext, execute
from support import build_config, database, role

SKILL_BODY = """# Deploy aman

1. Jalankan test.
2. Deploy.
"""


def make_config(root: Path) -> Config:
    return build_config(
        root, roles={"owner": role("owner")}, default_role="owner", workspace=root / "workspace"
    )


def add_file(db: Database, skill: str, path: str, content: str) -> None:
    with db.connect() as conn:
        db.upsert(
            conn,
            skill_files,
            [
                {
                    "skill": skill,
                    "path": path,
                    "content": content.encode("utf-8"),
                    "updated_at": 0.0,
                }
            ],
            ["skill", "path"],
        )


def test_parse_frontmatter_and_empty_store(tmp_path: Path) -> None:
    data, body = parse_frontmatter(
        "---\nname: deploy-aman\ndescription: Deploy service.\n---\n\n" + SKILL_BODY
    )
    assert data["name"] == "deploy-aman"
    assert body.startswith("# Deploy aman")
    assert parse_frontmatter("# tanpa frontmatter")[0] == {}

    store = SkillStore(database(tmp_path))
    assert store.names() == []
    assert store.index() == ""


def test_index_view_read_and_usage(tmp_path: Path) -> None:
    db = database(tmp_path)
    store = SkillStore(db)
    store.save("deploy-aman", "Deploy service dengan cek test dan rollback.", SKILL_BODY)
    add_file(db, "deploy-aman", "references/checklist.md", "cek dulu")

    assert "deploy-aman: Deploy service" in store.index()
    assert "dipakai" not in store.index()
    assert "# Deploy aman" in store.view("deploy-aman")
    assert "dipakai 1×" in store.index()
    assert "cek dulu" in store.read("deploy-aman", "references/checklist.md")
    with pytest.raises(PermissionError):
        store.read("deploy-aman", "../secret.txt")
    with pytest.raises(FileNotFoundError):
        store.read("deploy-aman", "references/missing.md")

    store.save("lain", "Skill kedua.", "# Lain\n")
    assert set(store.names()) == {"deploy-aman", "lain"}


def test_index_clips_at_budget(tmp_path: Path) -> None:
    store = SkillStore(database(tmp_path), max_index_chars=200)
    for index in range(12):
        store.save(f"skill-{index}", f"Prosedur nomor {index}.", f"# {index}\n")
    text = store.index()
    assert "- ..." in text
    assert len(text.splitlines()) < 13


def test_save_duplicate_and_overwrite(tmp_path: Path) -> None:
    store = SkillStore(database(tmp_path))
    assert store.save("deploy", "Deploy aman.", "# Deploy\n") == "deploy"
    assert "deploy" in store.names()
    with pytest.raises(FileExistsError):
        store.save("deploy", "Lagi.", "# Lagi\n")
    store.save("deploy", "Versi dua.", "# Dua\n", overwrite=True)
    assert "Versi dua." in store.index()
    with pytest.raises(ValueError):
        store.save("../escape", "x", "y")


def test_skill_tool_autonomous_lifecycle(tmp_path: Path) -> None:
    store = SkillStore(database(tmp_path))
    store.save("deploy", "Deploy aman.", "# Deploy\n")
    ctx = ToolContext(
        make_config(tmp_path), role("member"), ("skill",), chat_id="chat", skills=store
    )

    async def call(args: dict) -> str:
        return await execute(TOOLS.by_name, "skill", args, ctx)

    assert "deploy: Deploy aman." in asyncio.run(call({"action": "list"}))
    assert "# Deploy" in asyncio.run(call({"action": "view", "name": "deploy"}))
    assert "skill disimpan" in asyncio.run(
        call({"action": "save", "name": "lain", "description": "Lain.", "body": "# Lain\n"})
    )
    assert "skill disimpan" in asyncio.run(
        call(
            {
                "action": "save",
                "name": "deploy",
                "description": "Deploy v2.",
                "body": "# V2\n",
                "overwrite": True,
            }
        )
    )
    assert "Deploy v2." in store.index()
    assert "diarsipkan" in asyncio.run(call({"action": "archive", "name": "lain"}))
    assert "lain" not in store.names()


def test_skill_command_list_view_and_read(tmp_path: Path) -> None:
    db = database(tmp_path)
    store = SkillStore(db)
    store.save("deploy", "Deploy aman.", "# Deploy\n")
    add_file(db, "deploy", "refs/x.md", "detail")

    from types import SimpleNamespace

    class FakeApp:
        skills = store
        soul = SimpleNamespace(language="id")

    def out(args: str) -> str:
        ctx = CommandContext(
            agent=FakeApp(),
            gateway=None,
            roles=(role("owner"),),
            chat_id="chat",
            args=args,
            commands=(),
            tools=(),
        )
        return asyncio.run(SKILLCMD.fn(ctx))

    assert "deploy: Deploy aman." in out("")
    assert "# Deploy" in out("deploy")
    assert "detail" in out("read deploy refs/x.md")
