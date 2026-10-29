"""Kontrak adapter Mem0: config, scope/metadata, infer, supersedes, privasi forget."""

from __future__ import annotations

import asyncio
import sys
from types import SimpleNamespace

import pytest

from sanka.engine.config import DatabaseConfig
from sanka.engine.memory import context as memory_context
from sanka.engine.memory.mem0 import Mem0Backend
from support import FakeMem0, build_config

PG = "postgresql+psycopg://sanka:secret@db:5432/sanka"


def make_backend(tmp_path, monkeypatch) -> tuple[Mem0Backend, FakeMem0]:
    monkeypatch.setenv("K", "key")
    config = build_config(tmp_path, database_config=DatabaseConfig(url=PG, url_env=""))
    backend = Mem0Backend(config=config, db=None)
    fake = FakeMem0()
    backend._memory = fake
    return backend, fake


def test_config_dict_maps_providers(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    body = backend._config_dict()

    assert body["vector_store"]["provider"] == "pgvector"
    store = body["vector_store"]["config"]
    assert store["connection_string"] == "postgresql://sanka:secret@db:5432/sanka"
    assert store["collection_name"] == "sanka_memories"
    assert store["embedding_model_dims"] == 384
    assert body["llm"]["provider"] == "openai"
    assert body["llm"]["config"]["model"] == "m"
    assert body["llm"]["config"]["openai_base_url"] == "http://x"
    assert body["llm"]["config"]["api_key"] == "key"
    assert body["embedder"]["provider"] == "huggingface"
    assert body["embedder"]["config"]["embedding_dims"] == 384


def test_ready_sets_opencode_session_header(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)

    class FakeAsyncMemory:
        def __init__(self) -> None:
            self.llm = SimpleNamespace(client=None)

        @classmethod
        def from_config(cls, body):
            return cls()

    monkeypatch.setitem(sys.modules, "mem0", SimpleNamespace(AsyncMemory=FakeAsyncMemory))
    asyncio.run(backend.ready())

    client = backend._memory.llm.client
    assert client.default_headers["x-opencode-session"] == "sanka-agent-mem0"
    client.close()


def test_add_maps_identity_scope_and_infer_false(tmp_path, monkeypatch) -> None:
    backend, fake = make_backend(tmp_path, monkeypatch)

    ids = asyncio.run(
        backend.add(
            "suka kopi tanpa gula",
            scope="person",
            kind="preference",
            person_id="628111",
            chat_id="628111@s.whatsapp.net",
            source="tool",
        )
    )

    assert ids == ["m1"]
    _verb, call = fake.calls[0]
    assert call["user_id"] == "person:628111"
    assert call["infer"] is False
    assert call["metadata"]["scope"] == "person"
    assert call["metadata"]["audience"] == "private"
    assert call["metadata"]["kind"] == "preference"
    assert call["metadata"]["where"] is None
    assert call["metadata"]["level"] == 2
    assert call["metadata"]["owner_id"] == "628111"
    assert call["metadata"]["subject_id"] == "628111"


def test_capture_infers_and_uses_person_identity(tmp_path, monkeypatch) -> None:
    backend, fake = make_backend(tmp_path, monkeypatch)

    asyncio.run(
        backend.capture(
            [{"role": "user", "content": "aku suka kopi"}, {"role": "assistant", "content": "noted"}],
            person_id="628111",
            chat_id="g@g.us",
            audience="group",
        )
    )

    _verb, call = fake.calls[0]
    assert call["infer"] is True
    assert call["user_id"] == "person:628111"
    assert call["metadata"]["audience"] == "group"


def test_sensitive_text_rejected(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="secret"):
        asyncio.run(
            backend.add("api_key=abc123", scope="global", source="tool")
        )
    with pytest.raises(ValueError, match="level 5"):
        asyncio.run(
            backend.add("level lima", scope="global", source="tool", level=5)
        )


def test_supersedes_deletes_old_before_add(tmp_path, monkeypatch) -> None:
    backend, fake = make_backend(tmp_path, monkeypatch)
    old = asyncio.run(
        backend.add("suka kopi", scope="person", person_id="1", chat_id="c")
    )[0]

    asyncio.run(
        backend.add(
            "suka teh",
            scope="person",
            person_id="1",
            chat_id="c",
            supersedes=old,
        )
    )

    assert old not in fake.items
    assert any(item["memory"] == "suka teh" for item in fake.items.values())


def test_supersedes_rejects_other_person(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    old = asyncio.run(
        backend.add("punya budi", scope="person", person_id="1", chat_id="c")
    )[0]

    with pytest.raises(ValueError, match="another person"):
        asyncio.run(
            backend.add(
                "punya ani",
                scope="person",
                person_id="2",
                chat_id="c",
                supersedes=old,
            )
        )


def test_forget_checks_scope_owner(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    memory_id = asyncio.run(
        backend.add("fakta pribadi", scope="person", person_id="1", chat_id="c")
    )[0]
    global_id = asyncio.run(backend.add("fakta global", scope="global", chat_id="c"))[0]

    assert asyncio.run(backend.forget(memory_id, requester_id="2")) is False
    assert asyncio.run(backend.forget("tidak-ada", requester_id="1")) is False
    assert asyncio.run(backend.forget(global_id, requester_id="1")) is False
    assert asyncio.run(backend.forget(global_id, requester_id="1", allow_global=True)) is True
    assert asyncio.run(backend.forget(memory_id, requester_id="1")) is True


def test_search_queries_only_requested_scopes(tmp_path, monkeypatch) -> None:
    backend, fake = make_backend(tmp_path, monkeypatch)
    asyncio.run(backend.add("global fact", scope="global", chat_id="chat"))
    asyncio.run(backend.add("person fact", scope="person", person_id="1", chat_id="chat"))
    asyncio.run(backend.add("chat fact", scope="chat", chat_id="chat"))

    hits = asyncio.run(backend.search("fact", person_id="1", chat_id="chat"))
    assert {hit.scope for hit in hits} == {"global", "person", "chat"}

    only_global = asyncio.run(
        backend.search("fact", person_id="1", chat_id="chat", scopes=("global",))
    )
    assert [hit.scope for hit in only_global] == ["global"]
    filters = [call[1]["filters"] for call in fake.calls if call[0] in {"search", "get_all"}]
    assert {"agent_id": "sanka"} in filters


def test_context_timeout_returns_empty(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    monkeypatch.setattr(memory_context, "TIMEOUT", 0.01)

    class Slow(FakeMem0):
        async def search(self, query, **_kwargs):
            await asyncio.sleep(0.2)
            return {"results": []}

    backend._memory = Slow()
    assert asyncio.run(
        backend.context(person_id="1", chat_id="c", query="apa")
    ).text == ""
