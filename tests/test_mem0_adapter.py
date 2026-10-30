"""Kontrak adapter Mem0: config, scope/metadata, infer, supersedes, privasi forget."""

from __future__ import annotations

import asyncio
import re
import sys
from types import SimpleNamespace

import pytest
from sqlalchemy import text as sql_text

from sanka.engine.config import DatabaseConfig
from sanka.engine.memory import context as memory_context
from sanka.engine.memory.mem0 import Mem0Backend
from support import FakeMem0, build_config, database

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


# ---------------------------------------------------------------- id resolve

UUID_A = "5d94099b-2441-4479-b058-119e8f56ef89"
UUID_B = "5d94099b-3300-4479-b058-119e8f56ef89"  # prefix 8 sama dengan UUID_A
UUID_C = "7577bf34-1111-4479-b058-119e8f56ef89"
UUID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", re.IGNORECASE
)


class IdStrictMem0(FakeMem0):
    """Meniru pgvector: id selain UUID penuh membuat query gagal."""

    def __init__(self) -> None:
        super().__init__()
        self.seen: list[str] = []

    def _check(self, memory_id) -> None:
        self.seen.append(str(memory_id))
        if not UUID_RE.match(str(memory_id)):
            raise RuntimeError(f'invalid input syntax for type uuid: "{memory_id}"')

    async def get(self, memory_id):
        self._check(memory_id)
        return await super().get(memory_id)

    async def delete(self, memory_id):
        self._check(memory_id)
        return await super().delete(memory_id)

    async def update(self, memory_id, **kwargs):
        self._check(memory_id)
        return await super().update(memory_id, **kwargs)


def _item(memory_id: str, text: str, **meta) -> dict:
    metadata = {
        "scope": "person",
        "kind": "fact",
        "level": 2,
        "confirmed": True,
        "owner_id": "1",
        "subject_id": "1",
        "person_id": "1",
    }
    metadata.update(meta)
    return {"id": memory_id, "memory": text, "metadata": metadata, "created_at": 1.0}


def make_resolving(tmp_path, monkeypatch, *, table_ids=(), items=()) -> tuple[Mem0Backend, IdStrictMem0]:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    db = database(tmp_path)
    with db.engine.begin() as conn:
        conn.execute(sql_text("CREATE TABLE sanka_memories (id TEXT PRIMARY KEY)"))
        for value in table_ids:
            conn.execute(sql_text("INSERT INTO sanka_memories (id) VALUES (:id)"), {"id": value})
    backend.db = db
    fake = IdStrictMem0()
    for item in items:
        fake.items[item["id"]] = item
    backend._memory = fake
    return backend, fake


def test_full_uuid_skips_the_lookup(tmp_path, monkeypatch) -> None:
    backend, fake = make_resolving(
        tmp_path, monkeypatch, items=[_item(UUID_A, "suka kopi")]
    )
    backend.db = None  # lookup must not be needed for a full uuid

    assert asyncio.run(backend.get(UUID_A)).id == UUID_A
    assert fake.seen == [UUID_A]


def test_prefix_resolves_to_the_full_uuid(tmp_path, monkeypatch) -> None:
    backend, fake = make_resolving(
        tmp_path, monkeypatch, table_ids=[UUID_A], items=[_item(UUID_A, "suka kopi")]
    )

    hit = asyncio.run(backend.get("#5d94099b"))

    assert hit is not None and hit.id == UUID_A
    assert fake.seen == [UUID_A]


def test_bare_32_hex_is_dashed_not_queried(tmp_path, monkeypatch) -> None:
    backend, fake = make_resolving(
        tmp_path, monkeypatch, items=[_item(UUID_A, "suka kopi")]
    )

    hit = asyncio.run(backend.get(UUID_A.replace("-", "").upper()))

    assert hit is not None and hit.id == UUID_A
    assert fake.seen == [UUID_A]


def test_ambiguous_or_missing_prefix_never_reaches_mem0(tmp_path, monkeypatch) -> None:
    backend, fake = make_resolving(
        tmp_path, monkeypatch, table_ids=[UUID_A, UUID_B, UUID_C]
    )

    assert asyncio.run(backend.get("5d94099b")) is None  # ambigu
    assert asyncio.run(backend.get("deadbeef")) is None  # tiada
    assert asyncio.run(backend.get("")) is None
    assert fake.seen == []  # prefix ambigu/tiada tidak pernah sampai ke Mem0
    # id non-hex bukan prefix uuid: diteruskan seperti sebelumnya
    assert backend._resolve_id("7577bf34zz") == "7577bf34zz"


def test_forget_and_veto_prefix_use_the_full_uuid(tmp_path, monkeypatch) -> None:
    backend, fake = make_resolving(
        tmp_path,
        monkeypatch,
        table_ids=[UUID_A, UUID_C],
        items=[_item(UUID_A, "fakta pribadi"), _item(UUID_C, "fakta lain")],
    )

    assert asyncio.run(backend.forget("5d94099b", requester_id="1")) is True
    assert asyncio.run(backend.veto("7577bf34", subject_id="1")) is True

    assert UUID_A not in fake.items
    assert fake.items[UUID_C]["metadata"]["vetoed"] is True
    assert set(fake.seen) == {UUID_A, UUID_C}


def test_non_uuid_id_is_passed_through(tmp_path, monkeypatch) -> None:
    backend, _fake = make_backend(tmp_path, monkeypatch)
    backend._memory.items["m7"] = _item("m7", "fakta")

    assert asyncio.run(backend.get("m7")).id == "m7"
