"""Kontrak memori lintas chat: person ikut ke mana pun, rahasia grup tetap di grup."""

from __future__ import annotations

import asyncio

from sanka.engine.config import DatabaseConfig
from sanka.engine.memory.mem0 import Mem0Backend
from support import FakeMem0, build_config

DM = "628111@s.whatsapp.net"
GROUP_A = "111@g.us"
GROUP_B = "222@g.us"


def backend(tmp_path):
    config = build_config(
        tmp_path, database_config=DatabaseConfig(url="postgresql+psycopg://u:p@db:5432/sanka", url_env="")
    )
    found = Mem0Backend(config=config, db=None)
    found._memory = FakeMem0()
    return found


def seed(memory: Mem0Backend) -> None:
    asyncio.run(memory.add("owner suka kopi tanpa gula", scope="person", person_id="628111", chat_id=DM))
    asyncio.run(memory.add("panitia punya grup logistik", scope="global", chat_id=DM))
    asyncio.run(memory.add("rahasia grup A: tiket konser", scope="chat", chat_id=GROUP_A))
    asyncio.run(memory.add("orang lain suka teh", scope="person", person_id="628999", chat_id="628999@s.whatsapp.net"))


def test_person_memory_follows_owner_across_chats(tmp_path) -> None:
    memory = backend(tmp_path)
    seed(memory)

    hits = asyncio.run(memory.search("kopi", person_id="628111", chat_id=GROUP_B))
    texts = {hit.text for hit in hits}
    assert "owner suka kopi tanpa gula" in texts
    assert "panitia punya grup logistik" in texts
    assert "rahasia grup A: tiket konser" not in texts
    assert "orang lain suka teh" not in texts


def test_chat_memory_stays_in_its_chat(tmp_path) -> None:
    memory = backend(tmp_path)
    seed(memory)

    inside = asyncio.run(memory.search("tiket", person_id="628111", chat_id=GROUP_A))
    assert "rahasia grup A: tiket konser" in {hit.text for hit in inside}
    outside = asyncio.run(memory.search("tiket", person_id="628111", chat_id=GROUP_B))
    assert "rahasia grup A: tiket konser" not in {hit.text for hit in outside}


def test_stranger_only_gets_global(tmp_path) -> None:
    memory = backend(tmp_path)
    seed(memory)

    hits = asyncio.run(memory.search("kopi", person_id="777", chat_id=GROUP_B))
    assert {hit.scope for hit in hits} <= {"global"}


def test_context_renders_scopes_and_dates(tmp_path) -> None:
    memory = backend(tmp_path)
    seed(memory)

    block = asyncio.run(
        memory.context(person_id="628111", chat_id=GROUP_B, query="kopi", max_items=10, max_chars=2000)
    )

    assert block.text.startswith("Long-term memory (cross-chat):")
    assert "owner suka kopi tanpa gula" in block.text
    assert "[person/" in block.text and "[global/" in block.text
    assert "L2" in block.text
    assert "orang lain suka teh" not in block.text
    assert "tiket konser" not in block.text


def test_context_respects_char_budget(tmp_path) -> None:
    memory = backend(tmp_path)
    asyncio.run(memory.add("x" * 200, scope="global", chat_id=DM))

    block = asyncio.run(
        memory.context(person_id="628111", chat_id=GROUP_B, query="x", max_items=10, max_chars=60)
    )
    assert block.text == ""


def test_group_captured_person_memory_stays_in_that_group(tmp_path) -> None:
    memory = backend(tmp_path)
    asyncio.run(
        memory._memory.add(
            [{"role": "user", "content": "rahasia grup: kode pos"}],
            metadata={
                "scope": "person",
                "kind": "fact",
                "audience": "group",
                "person_id": "628111",
                "chat_id": GROUP_A,
            },
            user_id="person:628111",
        )
    )

    inside = asyncio.run(memory.search("kode", person_id="628111", chat_id=GROUP_A))
    outside = asyncio.run(memory.search("kode", person_id="628111", chat_id=GROUP_B))

    assert any("kode pos" in hit.text for hit in inside)
    assert not any("kode pos" in hit.text for hit in outside)
