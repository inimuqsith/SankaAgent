"""Kontrak skill: frontmatter, indeks, progressive disclosure, file pendukung, dan save."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sanka.commands import CommandContext
from sanka.commands.skill import COMMAND as SKILLCMD
from sanka.config import Config, LLMConfig, RoleConfig, SandboxConfig, WhatsAppConfig
from sanka.skills import SkillStore, parse_frontmatter
from sanka.tools import REGISTRY as TOOLS
from sanka.tools import ToolContext, execute

SKILL = """---
name: deploy-aman
description: Deploy service dengan cek test dan rollback.
---

# Deploy aman

1. Jalankan test.
2. Deploy.
"""


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


def test_parse_frontmatter_and_invalid_files(tmp_path: Path) -> None:
    data, body = parse_frontmatter(SKILL)
    assert data["name"] == "deploy-aman"
    assert body.startswith("# Deploy aman")
    assert parse_frontmatter("# tanpa frontmatter")[0] == {}

    root = tmp_path / "skills"
    (root / "bad").mkdir(parents=True)
    (root / "bad" / "SKILL.md").write_text("# tanpa frontmatter", encoding="utf-8")
    store = SkillStore(root)
    assert store.names() == []


def test_index_view_read_and_refresh(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    folder = root / "deploy"
    folder.mkdir(parents=True)
    (folder / "SKILL.md").write_text(SKILL, encoding="utf-8")
    (folder / "references").mkdir()
    (folder / "references" / "checklist.md").write_text("cek dulu", encoding="utf-8")
    outside = tmp_path / "secret.txt"
    outside.write_text("rahasia", encoding="utf-8")

    store = SkillStore(root)
    assert "deploy-aman: Deploy service" in store.index()
    assert "dipakai" not in store.index()
    assert "# Deploy aman" in store.view("deploy-aman")
    assert "dipakai 1×" in store.index()
    assert (root / ".usage.json").is_file()
    assert "cek dulu" in store.read("deploy-aman", "references/checklist.md")
    with pytest.raises(PermissionError):
        store.read("deploy-aman", "../secret.txt")

    second = root / "lain"
    second.mkdir()
    (second / "SKILL.md").write_text(
        "---\nname: lain\ndescription: Skill kedua.\n---\n\n# Lain\n",
        encoding="utf-8",
    )
    assert set(store.names()) == {"deploy-aman", "lain"}


def test_save_duplicate_and_overwrite(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    path = store.save("deploy", "Deploy aman.", "# Deploy\n")
    assert path.is_file()
    assert "deploy" in store.names()
    with pytest.raises(FileExistsError):
        store.save("deploy", "Lagi.", "# Lagi\n")
    store.save("deploy", "Versi dua.", "# Dua\n", overwrite=True)
    assert "Versi dua." in store.index()
    with pytest.raises(ValueError):
        store.save("../escape", "x", "y")


def test_skill_tool_autonomous_lifecycle(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    store.save("deploy", "Deploy aman.", "# Deploy\n")
    ctx = ToolContext(make_config(tmp_path), RoleConfig("member"), ("skill",), chat_id="chat", skills=store)

    async def call(args: dict) -> str:
        return await execute(TOOLS.by_name, "skill", args, ctx)

    assert "deploy: Deploy aman." in asyncio.run(call({"action": "list"}))
    assert "# Deploy" in asyncio.run(call({"action": "view", "name": "deploy"}))
    assert "skill disimpan" in asyncio.run(
        call({"action": "save", "name": "lain", "description": "Lain.", "body": "# Lain\n"})
    )
    assert "skill disimpan" in asyncio.run(
        call({"action": "save", "name": "deploy", "description": "Deploy v2.", "body": "# V2\n", "overwrite": True})
    )
    assert "Deploy v2." in store.index()
    assert "skill diarsipkan" in asyncio.run(call({"action": "archive", "name": "lain"}))
    assert "lain" not in store.names()


def test_skill_command_list_view_and_read(tmp_path: Path) -> None:
    store = SkillStore(tmp_path / "skills")
    folder = store.save("deploy", "Deploy aman.", "# Deploy\n")
    (folder.parent / "refs").mkdir()
    (folder.parent / "refs" / "x.md").write_text("detail", encoding="utf-8")

    class FakeApp:
        skills = store

    def out(args: str) -> str:
        return asyncio.run(SKILLCMD.fn(CommandContext(FakeApp(), "chat", RoleConfig("owner"), args, (), ())))

    assert "deploy: Deploy aman." in out("")
    assert "# Deploy" in out("deploy")
    assert "detail" in out("read deploy refs/x.md")
