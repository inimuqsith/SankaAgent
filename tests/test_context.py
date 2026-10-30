"""Kontrak payload: peran penulis dan visibilitas, bukan flag `outgoing` transport."""

from __future__ import annotations

import asyncio
from datetime import datetime

from sanka.engine.context import (
    DEFAULT_VISIBILITY,
    ChatMessage,
    build_user_content,
    prompt_entry,
)
from sanka.engine.soul import Soul
from support import build_config


def message(
    mid: str,
    text: str,
    *,
    sender_name: str = "Budi",
    sender_id: str = "628222",
    author: str = "unknown",
    visibility: int = 2,
) -> ChatMessage:
    return ChatMessage(
        id=mid,
        sender_name=sender_name,
        sender_id=sender_id,
        when=datetime(2026, 1, 1, 10),
        text=text,
        author=author,
        visibility=visibility,
    )


def test_chat_message_normalizes_author_and_clamps_visibility() -> None:
    entry = ChatMessage("a", "Budi", "1", datetime(2026, 1, 1), "x", author="ghost", visibility=9)
    low = ChatMessage("b", "Budi", "1", datetime(2026, 1, 1), "x", visibility=-3)
    broken = ChatMessage("c", "Budi", "1", datetime(2026, 1, 1), "x", visibility="tinggi")  # type: ignore[arg-type]

    assert entry.author == "unknown"
    assert entry.visibility == 4
    assert low.visibility == 0
    assert broken.visibility == DEFAULT_VISIBILITY


def test_payload_tags_manual_owner_without_calling_it_kamu(tmp_path) -> None:
    config = build_config(tmp_path, soul=Soul(name="sanka", persona="p"))
    manual = message("m1", "aku ketik sendiri", author="manual_owner")
    bot = message("b1", "halo", sender_name="sanka", sender_id="628567770837", author="bot")

    text = asyncio.run(build_user_content(config, "chat", [manual, bot], bot))

    manual_head = next(line for line in text.splitlines() if line.startswith("[2026-01-01 10:00]"))
    bot_head = next(line for line in text.splitlines() if "role=bot" in line)
    assert "role=manual_owner" in manual_head
    assert "kamu" not in manual_head
    assert bot_head.startswith("[2026-01-01 10:00] sanka (628567770837) [role=bot")
    assert "kamu" in bot_head


def test_prompt_entry_never_calls_manual_owner_kamu() -> None:
    manual = prompt_entry(message("m1", "aku manual", author="manual_owner"), "sanka")
    bot = prompt_entry(
        message("b1", "halo", sender_name="sanka", sender_id="1", author="bot"), "sanka"
    )

    assert manual.startswith("[role=manual_owner vis=L2] Budi (owner manual): aku manual")
    assert "kamu" not in manual
    assert bot.startswith("[role=bot vis=L2] kamu (sanka): halo")


def test_payload_visibility_tag_follows_the_entry(tmp_path) -> None:
    config = build_config(tmp_path, soul=Soul(name="sanka", persona="p"))
    entry = message("m1", "aib", author="manual_owner", visibility=3)

    text = asyncio.run(build_user_content(config, "chat", [entry], entry))

    assert "role=manual_owner vis=L3" in text


def test_working_memory_merge_upgrades_authorship_and_visibility() -> None:
    from sanka.engine.context import WorkingMemory

    old = message("a", "halo", sender_name="Owner", sender_id="62")
    manual = message("a", "", sender_name="Owner Manual", author="manual_owner", visibility=3)
    bot = message("a", "", sender_name="sanka", sender_id="9", author="bot")

    first = WorkingMemory._merge(old, manual)
    second = WorkingMemory._merge(first, bot)
    downgrade = WorkingMemory._merge(second, message("a", ""))

    assert (first.author, first.visibility) == ("manual_owner", 3)
    assert (second.author, second.sender_name, second.sender_id) == ("bot", "sanka", "9")
    assert downgrade.author == "bot"
    assert downgrade.sender_name == "sanka"
    assert downgrade.visibility == 3
    assert downgrade.text == "halo"
