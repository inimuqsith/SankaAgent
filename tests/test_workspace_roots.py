"""Kontrak root workspace per role: public read-only, scratch per chat, private trusted-only."""

from __future__ import annotations

import asyncio
from pathlib import Path

from support import make_agent, role

from sanka.engine.registry import Handler
from sanka.engine.tools import execute, safe_chat
from sanka.gateways.whatsapp.tools.send import _send
from sanka.gateways.whatsapp.tools.send_to import _send_to

SEND = Handler(
    name="send",
    family="write",
    description="test",
    fn=_send,
    parameters={"type": "object", "properties": {"path": {"type": "string"}}},
)
SEND_TO = Handler(
    name="send_to",
    family="write",
    description="test",
    fn=_send_to,
    parameters={"type": "object", "properties": {"chat": {"type": "string"}}},
)


def member_env(tmp_path):
    return make_agent(
        tmp_path,
        roles={
            "member": role(
                "member", tools=("read", "write", "send", "send_to"), commands=("*",)
            )
        },
        default_role="member",
    )


def member_ctx(env, chat_id: str = "111@g.us"):
    return env.agent.tool_context(
        chat_id, "628111", (env.config.roles.get("member"),), env.gateway
    )


def call(env, name: str, args: dict, ctx):
    specs = dict(env.agent.tools.by_name)
    specs["send"] = SEND
    specs["send_to"] = SEND_TO
    return asyncio.run(execute(specs, name, args, ctx))


def test_member_reads_public_and_writes_scratch(tmp_path) -> None:
    env = member_env(tmp_path)
    (env.config.workspace / "public" / "x.txt").write_text("isi publik", encoding="utf-8")
    ctx = member_ctx(env)

    read = call(env, "read", {"path": "public/x.txt"}, ctx)
    write = call(env, "write", {"path": "scratch/a.txt", "content": "halo"}, ctx)
    read_again = call(env, "read", {"path": "scratch/a.txt"}, ctx)

    assert "isi publik" in read
    assert write.startswith("ok:")
    assert "halo" in read_again
    assert (env.config.workspace / "scratch" / safe_chat(ctx.chat_id) / "a.txt").is_file()


def test_member_bare_path_is_relative_to_scratch(tmp_path) -> None:
    env = member_env(tmp_path)
    ctx = member_ctx(env)

    write = call(env, "write", {"path": "catatan.txt", "content": "bare"}, ctx)
    read = call(env, "read", {"path": "catatan.txt"}, ctx)

    assert write.startswith("ok:")
    assert "bare" in read
    target = env.config.workspace / "scratch" / safe_chat(ctx.chat_id) / "catatan.txt"
    assert target.is_file()


def test_member_cannot_read_private_paths(tmp_path) -> None:
    env = member_env(tmp_path)
    for name in ("attachments", "whatsapp"):
        target = env.config.workspace / name / "x.txt"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("rahasia", encoding="utf-8")
    (env.config.workspace / "private").mkdir(parents=True, exist_ok=True)
    (env.config.workspace / "private" / "x.txt").write_text("rahasia", encoding="utf-8")
    ctx = member_ctx(env)

    for name in (
        "private/x.txt",
        str(env.config.workspace / "attachments" / "x.txt"),
        str(env.config.workspace / "whatsapp" / "x.txt"),
    ):
        result = call(env, "read", {"path": name}, ctx)
        assert result.startswith("error: path tidak diizinkan untuk role ini"), name


def test_member_cannot_write_public(tmp_path) -> None:
    env = member_env(tmp_path)
    ctx = member_ctx(env)

    result = call(env, "write", {"path": "public/x.txt", "content": "nope"}, ctx)

    assert result == "error: public hanya read-only untuk role ini"


def test_member_send_reads_public_but_not_private(tmp_path) -> None:
    env = member_env(tmp_path)
    (env.config.workspace / "public" / "ok.txt").write_text("ok", encoding="utf-8")
    private = env.config.workspace / "private" / "s.txt"
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("no", encoding="utf-8")
    ctx = member_ctx(env)

    public = call(env, "send", {"path": "public/ok.txt"}, ctx)
    denied = call(env, "send", {"path": "private/s.txt"}, ctx)

    assert not public.startswith("error:")
    assert denied.startswith("error: path tidak diizinkan untuk role ini")
    assert env.gateway.files[0][1].name == "ok.txt"


def test_member_cannot_send_to_another_chat(tmp_path) -> None:
    env = member_env(tmp_path)
    target = ("628999999999@s.whatsapp.net", "Budi")
    env.gateway.resolve_target = lambda query: (target, [])
    ctx = member_ctx(env)

    result = call(env, "send_to", {"chat": "budi", "text": "halo"}, ctx)

    assert result == "error: cross-chat send is only for trusted roles"


def test_trusted_role_keeps_full_workspace(tmp_path) -> None:
    env = make_agent(
        tmp_path,
        roles={"owner": role("owner", tools=("*",), commands=("*",))},
        default_role="owner",
    )
    (env.config.workspace / "attachments").mkdir(parents=True, exist_ok=True)
    (env.config.workspace / "attachments" / "x.txt").write_text("privat", encoding="utf-8")
    ctx = env.agent.tool_context(
        "111@g.us", "628111", (env.config.roles.get("owner"),), env.gateway
    )

    read = call(env, "read", {"path": "attachments/x.txt"}, ctx)
    write = call(env, "write", {"path": "public/owner.txt", "content": "boleh"}, ctx)

    assert "privat" in read
    assert write.startswith("ok:")
    assert (env.config.workspace / "public" / "owner.txt").read_text(encoding="utf-8") == (
        "boleh"
    )


def test_safe_chat_strips_path_separators() -> None:
    value = safe_chat("../../x/y")

    assert "/" not in value and "\\" not in value
    assert Path(value).name == value
    assert value not in {"", ".", ".."}
