"""Privacy contract: level matrix, fluidity, audit gate, confirmations, veto, credentials."""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

from sanka.engine.config import DatabaseConfig, PrivacyConfig
from sanka.engine.memory import MemoryHit
from sanka.engine.memory.mem0 import Mem0Backend
from sanka.engine.memory.privacy import PrivacyPolicy
from sanka.engine.roles import Role
from support import FakeLLM, FakeMem0, build_config, inbound_message, make_agent


def policy() -> PrivacyPolicy:
    return PrivacyPolicy(PrivacyConfig())


def hit(level: int, **kwargs) -> MemoryHit:
    base = {
        "id": f"m{level}",
        "text": "fakta",
        "level": level,
        "owner_id": "628111",
        "subject_id": "628111",
    }
    base.update(kwargs)
    return MemoryHit(**base)


def test_level_matrix_group_and_dm() -> None:
    rules = policy()

    assert rules.show(hit(2), is_dm=False) is True
    assert rules.disclose(hit(2), requester="999", is_dm=False) == "allow"
    assert rules.show(hit(3), is_dm=False) is False
    assert rules.disclose(hit(3), requester="628111", is_dm=False) == "ask"
    assert rules.disclose(hit(3), requester="999", is_dm=False) == "deny"
    assert rules.disclose(hit(3, confirmed=False), requester="628111", is_dm=False) == "deny"

    assert rules.disclose(hit(3), requester="628111", is_dm=True) == "allow"
    assert rules.disclose(hit(4, confirmed=False), requester="628111", is_dm=True) == "ask"
    assert rules.disclose(hit(4), requester="628111", is_dm=True) == "allow"
    assert rules.disclose(hit(4), requester="999", is_dm=True) == "deny"

    vetoed = hit(1, vetoed=True)
    assert rules.show(vetoed, is_dm=True) is False
    assert rules.disclose(vetoed, requester="628111", is_dm=True) == "deny"


class ScriptLLM(FakeLLM):
    """FakeLLM with scripted audit/assess/answer JSON."""

    def __init__(self, *, audit=None, assess=None, answer=None, text: str = "halo"):
        super().__init__(answer=text)
        self.audit = audit or {"mentions": [], "verdict": "allow", "reply": "", "question": ""}
        self.assess = assess or {"level": 2, "subject": "", "ask": False, "question": ""}
        self.answer_payload = answer or {"kind": "unclear", "level": 0}

    async def complete(self, system, user, **kwargs):
        self.decisions += 1
        if "audit" in system.lower():
            return json.dumps(self.audit)
        if "privacy of one chat turn" in system:
            return json.dumps(self.assess)
        if "Classify the sender" in system:
            return json.dumps(self.answer_payload)
        return await super().complete(system, user, **kwargs)


def review(env, inbound, reply, hits):
    return asyncio.run(env.agent.privacy.review(inbound, reply, hits, env.gateway))


def test_fluidity_level_0_2_never_calls_the_auditor(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    inbound = inbound_message("halo", chat_id="111@g.us", is_dm=False)

    result = review(env, inbound, "kopi ya", [hit(0), hit(2)])

    assert result.handled is False and result.reply == "kopi ya"
    assert env.agent.llm.decisions == 0


def test_group_level_3_without_audit_asks(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    inbound = inbound_message("halo", chat_id="111@g.us", is_dm=False)

    result = review(env, inbound, "si budi pernah bilang begitu", [hit(3)])

    assert result.handled is True
    assert env.gateway.sent
    assert "privat" in env.gateway.sent[0][1]
    approval = env.agent.approvals.latest("111@g.us", "628111")
    assert approval is not None and approval.kind == "disclosure"
    assert approval.payload["reply"] == "si budi pernah bilang begitu"


def test_dm_owner_level_3_stays_fluid(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    inbound = inbound_message("halo", chat_id="628111@s.whatsapp.net", is_dm=True)

    result = review(env, inbound, "ingat kopi kita", [hit(3)])

    assert result.handled is False and result.reply == "ingat kopi kita"
    assert env.agent.llm.decisions == 0


def test_vetoed_hit_is_denied_and_audit_rewrite_wins(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(
        audit={"mentions": [], "verdict": "allow", "reply": "aman kok", "question": ""}
    )
    inbound = inbound_message("halo", chat_id="111@g.us", is_dm=False)

    result = review(env, inbound, "dia pernah begitu", [hit(1, vetoed=True)])

    assert result.handled is False
    assert result.reply == "aman kok"


def test_disclosure_confirmation_sends_the_stored_draft(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "yes", "level": 0})
    inbound = inbound_message("halo", chat_id="111@g.us", is_dm=False)
    review(env, inbound, "draft rahasia", [hit(3)])

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("ya", chat_id="111@g.us", is_dm=False),
            env.gateway,
            "628111",
        )
    )

    assert handled is True
    assert env.gateway.sent[-1][1] == "draft rahasia"
    approval = env.agent.approvals.get(1)
    assert approval.status == "approved"


def test_other_sender_cannot_confirm(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "yes", "level": 0})
    inbound = inbound_message("halo", chat_id="111@g.us", is_dm=False)
    review(env, inbound, "draft rahasia", [hit(3)])

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("ya", chat_id="111@g.us", is_dm=False, sender="628999", person_id="628999"),
            env.gateway,
            "628999",
        )
    )

    assert handled is False
    assert len(env.gateway.sent) == 1
    assert env.agent.approvals.get(1).status == "pending"


def test_capture_level_4_asks_and_stores_unconfirmed(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(
        assess={"level": 4, "subject": "", "ask": True, "question": "seberapa rahasia?"}
    )
    inbound = inbound_message("aku pernah melakukan hal memalukan", chat_id="628111@s.whatsapp.net")

    asyncio.run(env.agent.privacy.capture_turn(inbound, "oke", env.gateway))

    call = env.memory.captures[0]
    assert call["level"] == 4 and call["confirmed"] is False
    assert call["owner_id"] == "628111"
    approval = env.agent.approvals.latest("628111@s.whatsapp.net", "628111")
    assert approval.kind == "privacy"
    assert env.gateway.sent[-1][1] == "seberapa rahasia?"


def test_capture_level_3_is_stored_without_a_question(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(assess={"level": 3, "subject": "", "ask": False})
    inbound = inbound_message("aku lagi banyak pikiran", chat_id="628111@s.whatsapp.net")

    asyncio.run(env.agent.privacy.capture_turn(inbound, "oke", env.gateway))

    assert env.memory.captures[0]["level"] == 3
    assert env.memory.captures[0]["confirmed"] is True
    assert env.agent.approvals.latest("628111@s.whatsapp.net", "628111") is None


def test_capture_skips_credentials_and_resolves_subject(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM()
    insecure = inbound_message("api_key=abc123", chat_id="628111@s.whatsapp.net")
    asyncio.run(env.agent.privacy.capture_turn(insecure, "ok", env.gateway))
    assert env.memory.captures == []

    env.agent.llm = ScriptLLM(assess={"level": 3, "subject": "Budi", "ask": False})
    env.agent.affect = SimpleNamespace(person_by_name=lambda chat_id, name: "628999")
    named = inbound_message("budi baru pindah kerja", chat_id="628111@s.whatsapp.net")
    asyncio.run(env.agent.privacy.capture_turn(named, "oh", env.gateway))
    assert env.memory.captures[0]["subject_id"] == "628999"


def test_privacy_confirmation_sets_level_and_confirmation(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(
        assess={"level": 4, "subject": "", "ask": True, "question": "seberapa rahasia?"},
        answer={"kind": "level", "level": 4},
    )
    inbound = inbound_message("aku pernah melakukan hal memalukan", chat_id="628111@s.whatsapp.net")
    asyncio.run(env.agent.privacy.capture_turn(inbound, "oke", env.gateway))
    memory_id = env.agent.approvals.latest("628111@s.whatsapp.net", "628111").args["memory_ids"][0]
    env.memory.hits[memory_id] = MemoryHit(
        id=memory_id, text="hal memalukan", level=4, owner_id="628111", confirmed=False
    )

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("rahasia", chat_id="628111@s.whatsapp.net"),
            env.gateway,
            "628111",
        )
    )

    assert handled is True
    hit_after = env.memory.hits[memory_id]
    assert hit_after.level == 4 and hit_after.confirmed is True
    assert env.agent.approvals.get(1).status == "approved"


def test_unclear_answer_keeps_pending(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(answer={"kind": "unclear", "level": 0})
    inbound = inbound_message("halo", chat_id="111@g.us", is_dm=False)
    review(env, inbound, "draft", [hit(3)])

    handled = asyncio.run(
        env.agent.privacy.handle_pending(
            inbound_message("hmm gimana ya", chat_id="111@g.us", is_dm=False),
            env.gateway,
            "628111",
        )
    )

    assert handled is True
    assert env.agent.approvals.get(1).status == "pending"
    assert "ya atau tidak" in env.gateway.sent[-1][1]


def test_memory_tool_requires_admin_for_global_and_subject_for_veto(tmp_path) -> None:
    from sanka.engine.tools.memory import _memory

    env = make_agent(tmp_path)
    member = env.config.roles.default_role()
    ctx = env.agent.tool_context(
        "chat", "628111", (member,), env.gateway, person_id="628111", is_dm=True
    )
    denied = asyncio.run(_memory({"action": "add", "scope": "global", "text": "fakta"}, ctx))
    assert "memory_admin" in denied

    owner = Role("owner", tools=("*",), memory_admin=True)
    owner_ctx = env.agent.tool_context(
        "chat", "628111", (owner,), env.gateway, person_id="628111", is_dm=True
    )
    saved = asyncio.run(
        _memory({"action": "add", "scope": "global", "text": "fakta", "level": 1}, owner_ctx)
    )
    assert saved.startswith("ok:")

    memory_id = next(reversed(env.memory.hits))
    env.memory.hits[memory_id] = MemoryHit(
        id=memory_id, text="fakta", subject_id="628999", owner_id="628111", level=1
    )
    wrong = asyncio.run(_memory({"action": "veto", "id": memory_id}, owner_ctx))
    assert "error:" in wrong

    subject_ctx = env.agent.tool_context(
        "chat", "628999", (member,), env.gateway, person_id="628999", is_dm=True
    )
    right = asyncio.run(_memory({"action": "veto", "id": memory_id}, subject_ctx))
    assert right.startswith("ok:")


def test_agent_turn_asks_then_sends_after_confirmation(tmp_path) -> None:
    env = make_agent(tmp_path)
    env.agent.llm = ScriptLLM(
        audit={"mentions": [], "verdict": "ask", "reply": "", "question": ""},
        answer={"kind": "yes", "level": 0},
        text="halo juga",
    )
    env.memory.hits["m3"] = MemoryHit(
        id="m3",
        text="aib budi",
        level=3,
        owner_id="628111",
        subject_id="628111",
        confirmed=True,
    )
    group = inbound_message(
        "halo sanka", chat_id="111@g.us", is_dm=False, is_mention=True
    )

    asyncio.run(env.agent.handle(group, env.gateway))

    assert env.gateway.sent[-1][1] == "Itu menyentuh hal privat. Boleh aku sebutkan di sini? Balas ya atau tidak."
    approval = env.agent.approvals.latest("111@g.us", "628111")
    assert approval.kind == "disclosure"

    asyncio.run(
        env.agent.handle(inbound_message("ya", chat_id="111@g.us", is_dm=False), env.gateway)
    )

    assert env.gateway.sent[-1][1] == "halo juga"
    assert env.agent.approvals.get(approval.id).status == "approved"


def test_vetoed_memory_is_excluded_from_context(tmp_path) -> None:
    config = build_config(
        tmp_path,
        database_config=DatabaseConfig(url="postgresql+psycopg://u:p@db:5432/sanka", url_env=""),
    )
    memory = Mem0Backend(config=config, db=None)
    memory._memory = FakeMem0()
    memory_id = asyncio.run(
        memory.add("rahasia kecil", scope="person", person_id="1", chat_id="c", level=1)
    )[0]

    assert asyncio.run(memory.veto(memory_id, subject_id="2")) is False
    assert asyncio.run(memory.veto(memory_id, subject_id="1")) is True
    context = asyncio.run(memory.context(person_id="1", chat_id="c", query="rahasia"))
    assert context.text == ""
    assert context.hits == ()
