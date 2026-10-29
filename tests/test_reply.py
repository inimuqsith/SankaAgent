"""Kontrak balasan: quote wajib wrapper event Neonize, target wajib chat asal."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path

from neonize.aioze.client import NewAClient
from neonize.proto.Neonize_pb2 import Message
from neonize.utils import Jid2String
from neonize.utils.enum import ChatPresence, ChatPresenceMedia

from sanka.config import (
    Config,
    LLMConfig,
    RoleConfig,
    SandboxConfig,
    WhatsAppConfig,
)
from sanka.decision import Appraisal, Decision, RelationshipDelta
from sanka.history import HistoryStore
from sanka.wa import App, Sessions, handle_message

BOT_JID = "628567770837@s.whatsapp.net"


def make_config() -> Config:
    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=Path("/tmp"),
        whatsapp=WhatsAppConfig(session_db=Path("/tmp/sanka-test.db")),
        roles={"member": RoleConfig("member")},
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )


def transport() -> tuple[NewAClient, list[tuple], list[tuple]]:
    """NewAClient asli dengan builder asli, tapi send_message dan presence diganti perekam."""
    client = NewAClient.__new__(NewAClient)
    sent: list[tuple] = []
    presence: list[tuple] = []

    async def send_message(to, message, link_preview=False, add_msg_secret=False):
        sent.append((to, message))

    async def send_chat_presence(jid, state, media):
        presence.append((Jid2String(jid), state, media))

    client.send_message = send_message
    client.send_chat_presence = send_chat_presence
    return client, sent, presence


async def reply_for(ev: Message) -> tuple[list[tuple], list[tuple]]:
    client, sent, presence = transport()
    app = App(make_config(), None, None, Sessions())
    app.bot_digits = {"628567770837"}
    await handle_message(client, ev, app)
    return sent, presence


def dm_event(text: str) -> Message:
    ev = Message()
    ev.Info.ID = "3EB0DM"
    ev.Info.MessageSource.Chat.User = "628999999999"
    ev.Info.MessageSource.Chat.Server = "s.whatsapp.net"
    ev.Info.MessageSource.Sender.User = "628999999999"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.conversation = text
    return ev


def group_event(text: str, mention: bool = True, event_id: str = "3EB0GROUP") -> Message:
    ev = Message()
    ev.Info.ID = event_id
    ev.Info.MessageSource.Chat.User = "1234567890"
    ev.Info.MessageSource.Chat.Server = "g.us"
    ev.Info.MessageSource.Sender.User = "628222222222"
    ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
    ev.Message.extendedTextMessage.text = text
    if mention:
        ev.Message.extendedTextMessage.contextInfo.mentionedJID.append(BOT_JID)
    return ev


def test_dm_reply_quotes_event_wrapper() -> None:
    ev = dm_event("/tidak-ada")
    sent, _ = asyncio.run(reply_for(ev))

    assert len(sent) == 1
    to, reply = sent[0]
    assert Jid2String(to) == "628999999999@s.whatsapp.net"
    context = reply.extendedTextMessage.contextInfo
    assert context.stanzaID == "3EB0DM"
    assert context.participant == "628999999999@s.whatsapp.net"
    assert context.quotedMessage.conversation == "/tidak-ada"
    assert reply.extendedTextMessage.text == "🤔 Command /tidak-ada tidak dikenal. Ketik /help untuk daftar command."


def test_group_reply_targets_group_not_sender() -> None:
    sent, _ = asyncio.run(reply_for(group_event("/tidak-ada")))

    assert len(sent) == 1
    to, reply = sent[0]
    assert Jid2String(to) == "1234567890@g.us"
    assert reply.extendedTextMessage.contextInfo.participant == "628222222222@s.whatsapp.net"
    assert reply.extendedTextMessage.contextInfo.quotedMessage.extendedTextMessage.text == "/tidak-ada"


def test_lid_sender_quotes_phone_alternative() -> None:
    ev = dm_event("/tidak-ada")
    ev.Info.MessageSource.Chat.User = "130799689031758"
    ev.Info.MessageSource.Chat.Server = "lid"
    ev.Info.MessageSource.Sender.User = "130799689031758"
    ev.Info.MessageSource.Sender.Server = "lid"
    ev.Info.MessageSource.SenderAlt.User = "628999999999"
    ev.Info.MessageSource.SenderAlt.Server = "s.whatsapp.net"

    sent, _ = asyncio.run(reply_for(ev))

    assert len(sent) == 1
    to, reply = sent[0]
    assert Jid2String(to) == "130799689031758@lid"
    assert reply.extendedTextMessage.contextInfo.participant == "628999999999@s.whatsapp.net"


def test_typing_presence_around_reply() -> None:
    _, presence = asyncio.run(reply_for(group_event("/tidak-ada")))

    assert [item[1] for item in presence] == [ChatPresence.CHAT_PRESENCE_COMPOSING, ChatPresence.CHAT_PRESENCE_PAUSED]
    assert {item[0] for item in presence} == {"1234567890@g.us"}
    assert {item[2] for item in presence} == {ChatPresenceMedia.CHAT_PRESENCE_MEDIA_TEXT}


def test_unaddressed_group_message_has_no_presence() -> None:
    _, presence = asyncio.run(reply_for(group_event("jawab ya", mention=False)))

    assert presence == []


def test_own_message_is_archived_without_reply(tmp_path) -> None:
    store = HistoryStore(tmp_path / "history.db", tmp_path)
    client, sent, presence = transport()
    app = App(make_config(), None, None, Sessions(store=store))
    app.bot_digits = {"628567770837"}
    ev = dm_event("dari ponsel sendiri")
    ev.Info.MessageSource.IsFromMe = True

    asyncio.run(handle_message(client, ev, app))

    assert sent == [] and presence == []
    stored = store.find("628999999999@s.whatsapp.net", "3EB0DM")
    assert stored is not None and stored.text == "dari ponsel sendiri" and stored.outgoing
    store.close()


GROUP = "1234567890@g.us"
DIRECT_MARKERS = ("mention asli ke kamu", "balasan ke pesanmu", "pesan pribadi (DM)")


def fake_decision(user: str) -> str:
    """Keputusan tiruan: balas hanya bila pesan jelas untuk Sanka (mention/DM/reply)."""
    direct = any(marker in str(user) for marker in DIRECT_MARKERS)
    return json.dumps(
        {
            "addressed": "me" if direct else "other",
            "act": "reply" if direct else "ignore",
            "mode": "daily",
            "tone": "neutral",
            "bubbles": 1,
            "appraisal": {"label": "neutral", "dP": 0.0, "dA": 0.0, "dD": 0.0, "note": ""},
            "relationship": {"trust": 0.0, "affection": 0.0, "respect": 0.0, "annoyance": 0.0},
            "reason": "tiruan",
        }
    )


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list = []

    async def complete(self, system, user, *, max_tokens=300, temperature=0.2, session="") -> str:
        return fake_decision(user)

    async def run(self, system, user_content, specs, ctx, session="") -> str:
        self.calls.append(user_content)
        return "siap"


class CaptureLLM:
    def __init__(self) -> None:
        self.system = ""
        self.ctx = None

    async def complete(self, system, user, *, max_tokens=300, temperature=0.2, session="") -> str:
        return fake_decision(user)

    async def run(self, system, user_content, specs, ctx, session="") -> str:
        self.system = system
        self.ctx = ctx
        return "siap"


class ReflectLLM(CaptureLLM):
    def __init__(self) -> None:
        super().__init__()
        self.reflected = 0
        self.contexts: list[str] = []
        self.memories: list[str] = []

    async def reflect(
        self, user_text: str, answer: str, context: str = "", memory: str = ""
    ) -> list[dict]:
        self.reflected += 1
        self.contexts.append(context)
        self.memories.append(memory)
        return []


class FakeHub:
    def specs(self, role) -> list:
        return []


def test_llm_gets_memory_skill_index_and_tool_context(tmp_path) -> None:
    from sanka.config import Config, LLMConfig, SandboxConfig, WhatsAppConfig
    from sanka.memory import MemoryStore
    from sanka.skills import SkillStore

    config = Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db"),
        roles={"owner": RoleConfig("owner", tools=("*",), commands=("*",))},
        default_role="owner",
        sandbox=SandboxConfig(),
        mcp={},
    )
    memory = MemoryStore(tmp_path / "memory.db")
    memory.add("global", "preference", "suka kopi")
    memory.add("628999999999@s.whatsapp.net", "fact", "rapat jam 3")
    skills = SkillStore(tmp_path / "skills")
    skills.save("deploy", "Deploy aman.", "# Deploy\n")
    llm = CaptureLLM()
    app = App(config, llm, FakeHub(), Sessions(), memory=memory, skills=skills)
    app.bot_digits = {"628567770837"}

    client, _, _ = transport()
    asyncio.run(handle_message(client, dm_event("halo"), app))

    assert "suka kopi" in llm.system
    assert "rapat jam 3" in llm.system
    assert "deploy: Deploy aman." in llm.system
    assert llm.ctx.memory is memory
    assert llm.ctx.skills is skills
    memory.close()


def test_member_gets_shared_global_and_chat_memory(tmp_path) -> None:
    from sanka.config import Config, LLMConfig, SandboxConfig, WhatsAppConfig
    from sanka.memory import MemoryStore
    from sanka.skills import SkillStore

    config = Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db"),
        roles={
            "owner": RoleConfig("owner", tools=("*",), commands=("*",)),
            "member": RoleConfig("member", tools=("memory", "skill", "send")),
        },
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )
    memory = MemoryStore(tmp_path / "memory.db")
    memory.add("global", "preference", "info global panitia")
    memory.add("628999999999@s.whatsapp.net", "fact", "info chat")
    skills = SkillStore(tmp_path / "skills")
    skills.save("deploy", "Deploy aman.", "# Deploy\n")
    llm = CaptureLLM()
    app = App(config, llm, FakeHub(), Sessions(), memory=memory, skills=skills)
    app.bot_digits = {"628567770837"}

    client, _, _ = transport()
    asyncio.run(handle_message(client, dm_event("halo"), app))

    assert "info chat" in llm.system
    assert "info global panitia" in llm.system
    assert "deploy: Deploy aman." in llm.system
    memory.close()


def app_with(llm: FakeLLM) -> App:
    app = App(make_config(), llm, FakeHub(), Sessions())
    app.bot_digits = {"628567770837"}
    return app


def test_role_without_memory_tool_does_not_reflect(tmp_path) -> None:
    from sanka.config import Config, LLMConfig, SandboxConfig, WhatsAppConfig
    from sanka.memory import MemoryStore

    config = Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db"),
        roles={"guest": RoleConfig("guest", tools=("read",))},
        default_role="guest",
        sandbox=SandboxConfig(),
        mcp={},
    )
    memory = MemoryStore(tmp_path / "memory.db")
    llm = ReflectLLM()
    app = App(config, llm, FakeHub(), Sessions(), memory=memory)
    app.bot_digits = {"628567770837"}

    client, _, _ = transport()
    asyncio.run(handle_message(client, dm_event("jadwal rapat panitia besok"), app))

    assert app.tasks == set()
    assert llm.reflected == 0
    memory.close()


def test_reflection_gets_recent_transcript(tmp_path) -> None:
    from sanka.config import Config, LLMConfig, SandboxConfig, WhatsAppConfig
    from sanka.memory import MemoryStore

    config = Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db", bot_name="sanka"),
        roles={"owner": RoleConfig("owner", tools=("*",), commands=("*",))},
        default_role="owner",
        sandbox=SandboxConfig(),
        mcp={},
    )
    memory = MemoryStore(tmp_path / "memory.db")
    memory.add("global", "fact", "panitia punya grup logistik")
    llm = ReflectLLM()
    app = App(config, llm, FakeHub(), Sessions(), memory=memory)
    app.bot_digits = {"628567770837"}
    client, _, _ = transport()

    async def flow() -> None:
        await handle_message(client, dm_event("catatan rapat jam 3"), app)
        await asyncio.gather(*app.tasks)
        second = dm_event("jadwal rapat panitia besok")
        second.Info.ID = "3EB0DM2"
        await handle_message(client, second, app)
        await asyncio.gather(*app.tasks)

    asyncio.run(flow())

    assert llm.reflected == 2
    assert llm.contexts[0] == ""
    assert "catatan rapat jam 3" in llm.contexts[1]
    assert "grup logistik" in llm.memories[1]
    memory.close()


def test_group_history_keeps_unaddressed_messages() -> None:
    async def flow() -> FakeLLM:
        client, _, _ = transport()
        llm = FakeLLM()
        app = app_with(llm)
        await handle_message(client, group_event("catatan rapat jam 3", mention=False), app)
        assert llm.calls == []
        await handle_message(client, group_event("@sanka ringkas ya"), app)
        return llm

    llm = asyncio.run(flow())

    assert len(llm.calls) == 1
    payload = llm.calls[0]
    assert "catatan rapat jam 3" in payload
    assert "ringkas ya" in payload


def test_quoted_message_is_rendered() -> None:
    async def flow() -> FakeLLM:
        client, _, _ = transport()
        llm = FakeLLM()
        app = app_with(llm)
        ev = group_event("@sanka ini apa?")
        context = ev.Message.extendedTextMessage.contextInfo
        context.stanzaID = "3EB0QUOTED"
        context.quotedMessage.conversation = "jawaban bot"
        context.participant = BOT_JID
        await handle_message(client, ev, app)
        return llm

    llm = asyncio.run(flow())

    assert len(llm.calls) == 1
    payload = llm.calls[0]
    assert "membalas" in payload
    assert "jawaban bot" in payload


def test_quote_resolved_from_log_keeps_sender_and_time() -> None:
    async def flow() -> FakeLLM:
        client, _, _ = transport()
        llm = FakeLLM()
        app = app_with(llm)
        await handle_message(client, group_event("pertanyaan lama", event_id="3EB0OLD"), app)
        ev = group_event("@sanka jawab", event_id="3EB0NEW")
        context = ev.Message.extendedTextMessage.contextInfo
        context.stanzaID = "3EB0OLD"
        context.quotedMessage.conversation = "pertanyaan lama"
        await handle_message(client, ev, app)
        return llm

    llm = asyncio.run(flow())

    payload = llm.calls[-1]
    assert re.search(r'↳ membalas \[\d{4}-\d\d-\d\d \d\d:\d\d\] 628222222222: "pertanyaan lama"', payload)


def lid_group_event(text: str, event_id: str = "3EB0LID") -> Message:
    ev = Message()
    ev.Info.ID = event_id
    ev.Info.MessageSource.Chat.User = "120363415422117951"
    ev.Info.MessageSource.Chat.Server = "g.us"
    ev.Info.MessageSource.Sender.User = "130799689031758"
    ev.Info.MessageSource.Sender.Server = "lid"
    ev.Message.extendedTextMessage.text = text
    ev.Message.extendedTextMessage.contextInfo.mentionedJID.append("73041086251252@lid")
    return ev


def test_reply_turns_number_into_native_lid_mention() -> None:
    class MentionLLM(FakeLLM):
        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.calls.append(user_content)
            return "siap, @+1 3079 96890 31758, ini buat kamu"

    async def flow():
        client, sent, _ = transport()
        llm = MentionLLM()
        app = app_with(llm)
        app.bot_digits.add("73041086251252")  # LID bot di grup LID
        await handle_message(client, lid_group_event("@Sanka tolong cek"), app)
        return sent

    sent = asyncio.run(flow())

    to, reply = sent[0]
    assert Jid2String(to) == "120363415422117951@g.us"
    context = reply.extendedTextMessage.contextInfo
    assert list(context.mentionedJID) == ["130799689031758@lid"]
    assert reply.extendedTextMessage.text == "siap, @130799689031758, ini buat kamu"
    assert context.stanzaID == "3EB0LID"  # quote tetap menempel


def test_reply_mentions_phone_sender_and_leaves_unknown_numbers() -> None:
    class MentionLLM(FakeLLM):
        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.calls.append(user_content)
            return "@628222222222 dan @999999999 dan @+6 2812 3456 7890"

    async def flow():
        client, sent, _ = transport()
        app = app_with(MentionLLM())
        await handle_message(client, group_event("pagi"), app)
        return sent

    sent = asyncio.run(flow())

    reply = sent[0][1]
    assert list(reply.extendedTextMessage.contextInfo.mentionedJID) == ["628222222222@s.whatsapp.net"]
    assert reply.extendedTextMessage.text == "@628222222222 dan @999999999 dan @6281234567890"


# --- keputusan otonom, emosi, bubble pendek, dan double-text ---------------------


def rich_config(
    tmp_path: Path,
    *,
    humanize=None,
    affect=None,
    roles: dict | None = None,
    default_role: str = "member",
) -> Config:
    from sanka.config import AffectConfig, HumanizeConfig

    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db"),
        roles=roles or {"member": RoleConfig("member", tools=("read",), commands=("help",))},
        default_role=default_role,
        sandbox=SandboxConfig(),
        mcp={},
        affect=affect or AffectConfig(enabled=False),
        humanize=humanize
        or HumanizeConfig(enabled=True, min_delay=0, max_delay=0, between_delay=0),
    )


def long_text(words: int = 120) -> str:
    return " ".join(["kata"] * words)


def payload_text(item) -> str:
    payload = item[1]
    return payload if isinstance(payload, str) else payload.extendedTextMessage.text


def verdict(
    act: str = "reply",
    *,
    addressed: str = "me",
    mode: str = "daily",
    tone: str = "neutral",
    bubbles: int = 1,
    label: str = "neutral",
    dP: float = 0.0,
    note: str = "",
    reason: str = "keputusan",
    annoyance: float = 0.0,
) -> str:
    return json.dumps(
        {
            "addressed": addressed,
            "act": act,
            "mode": mode,
            "tone": tone,
            "bubbles": bubbles,
            "appraisal": {"label": label, "dP": dP, "dA": 0.0, "dD": 0.0, "note": note},
            "relationship": {"trust": 0.0, "affection": 0.0, "respect": 0.0, "annoyance": annoyance},
            "reason": reason,
        }
    )


class DecisionLLM(FakeLLM):
    """LLM tiruan: keputusan tetap, balasan tetap, dan system prompt terekam."""

    def __init__(self, answer: str = "siap", decision: str | None = None) -> None:
        super().__init__()
        self.answer = answer
        self.decision = verdict() if decision is None else decision
        self.system = ""
        self.decisions: list[str] = []
        self.specs: list[list] = []

    async def complete(self, system, user, *, max_tokens=300, temperature=0.2, session="") -> str:
        self.decisions.append(user)
        return self.decision

    async def run(self, system, user_content, specs, ctx, session="") -> str:
        self.system = system
        self.specs.append(list(specs))
        self.calls.append(user_content)
        return self.answer


def affect_setup(tmp_path: Path):
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig

    config = AffectConfig(enabled=True)
    return AffectStore(tmp_path / "affect.db", config), config


def test_bubbles_follow_the_model_not_a_byte_counter(tmp_path) -> None:
    # Satu paragraf panjang tetap satu pesan (penjelasan serius tidak boleh dihancurkan),
    # dua paragraf jadi dua pesan berurutan seperti orang chat.
    long_answer = long_text(70)
    llm = DecisionLLM(answer=long_answer)

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == [long_answer]

    llm.answer = "bohong apanya sih 😭\n---\nmasa aku ngambek terus, nanti kamu bosen loh"

    async def flow_two() -> list[tuple]:
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        event = dm_event("masak kamu bohong")
        event.Info.ID = "3EB0BUBBLE"
        await handle_message(client, event, app)
        return sent

    sent = asyncio.run(flow_two())

    assert [payload_text(item) for item in sent] == [
        "bohong apanya sih 😭",
        "masa aku ngambek terus, nanti kamu bosen loh",
    ]
    assert sent[0][1].extendedTextMessage.contextInfo.stanzaID == "3EB0BUBBLE"
    assert isinstance(sent[1][1], str)  # bubble kedua tanpa quote


def test_unaddressed_group_skips_decision_and_reply(tmp_path) -> None:
    llm = DecisionLLM(decision=verdict("reply", addressed="group", reason="nimbrung"))

    async def flow():
        client, sent, presence = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("wujud aseli jomblo", mention=False), app)
        return sent, presence

    sent, presence = asyncio.run(flow())

    assert sent == [] and presence == []
    assert llm.decisions == []  # tanpa panggilan: model pun tidak ditanya


def test_group_name_is_fetched_when_archive_has_none(tmp_path) -> None:
    from neonize.proto.Neonize_pb2 import GroupInfo

    from sanka.history import HistoryStore

    llm = DecisionLLM()
    store = HistoryStore(tmp_path / "history.db", tmp_path)
    chat_id = "1234567890@g.us"
    calls: list[str] = []

    async def get_group_info(jid) -> GroupInfo:
        calls.append(Jid2String(jid))
        info = GroupInfo()
        info.GroupName.Name = "Grup Server"
        return info

    async def flow():
        client, _, _ = transport()
        client.get_group_info = get_group_info
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions(store=store))
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("@sanka halo", event_id="3EB0NAME1"), app)
        await handle_message(client, group_event("@sanka lagi", event_id="3EB0NAME2"), app)

    asyncio.run(flow())

    assert store.chat_name(chat_id) == "Grup Server"
    assert llm.calls[0].startswith('Konteks WhatsApp "Grup Server"')
    assert calls == [chat_id]  # turn kedua tidak menanya server lagi
    store.close()


def test_mention_decision_ignore_is_overridden_to_reply(tmp_path) -> None:
    llm = DecisionLLM(decision=verdict("ignore", addressed="other", tone="warm"))

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("@sanka halo"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["siap"]
    assert "Nada: curt" in llm.system
    assert llm.calls


def test_dm_decision_ignore_is_overridden_to_reply(tmp_path) -> None:
    llm = DecisionLLM(decision=verdict("ignore", addressed="other"))

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("lagi apa"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["siap"]
    assert "pesan pribadi (DM)" in llm.decisions[0]


def test_decision_invalid_mention_gets_real_attempt(tmp_path) -> None:
    llm = DecisionLLM(decision="")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("@sanka halo"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["siap"]
    assert len(llm.calls) == 1  # pesan yang dituju dapat percobaan nyata, bukan hmm


def test_long_styled_rekap_arrives_whole_and_verbatim(tmp_path) -> None:
    header = "📊 REKAP\n" + "━" * 20 + "\n"
    rekap = header + "\n".join(f"• baris {i} " + "data " * 200 for i in range(12))
    assert len(rekap) > 12_000
    llm = DecisionLLM(answer=rekap, decision=verdict("reply", mode="professional"))

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("rekap dong"), app)
        return sent

    sent = asyncio.run(flow())
    texts = [payload_text(item) for item in sent]

    assert len(sent) == 2  # 12k+ dipecah per 10k, bukan diringkas
    assert texts[0].startswith(header)  # gaya script utuh: emoji + garis
    assert " ".join("\n".join(texts).split()) == " ".join(rekap.split())  # isi utuh
    assert all(len(text) <= 10000 for text in texts)


def test_tool_send_empty_closing_confirms_not_kosong(tmp_path) -> None:
    class SentLLM(DecisionLLM):
        async def run(self, system, user_content, specs, ctx, session="") -> str:
            ctx.sent.append(
                {"tool": "send", "target": "chat", "kind": "file", "summary": "laporan.txt"}
            )
            return ""

    llm = SentLLM(decision=verdict("reply", mode="professional"))

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("kirim dong"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["Sudah kukirim."]


def test_decision_invalid_unaddressed_sends_nothing(tmp_path) -> None:
    llm = DecisionLLM(decision="")

    async def flow():
        client, sent, presence = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("wkwk", mention=False), app)
        return sent, presence

    sent, presence = asyncio.run(flow())

    assert sent == [] and presence == []
    assert llm.calls == []


def test_unprompted_group_leaves_emotion_untouched(tmp_path) -> None:
    store, affect_config = affect_setup(tmp_path)
    llm = DecisionLLM(
        decision=verdict(
            "ignore", addressed="group", label="hostility", dP=-0.4, annoyance=0.15
        )
    )

    async def flow():
        client, sent, presence = transport()
        app = App(
            rich_config(tmp_path, affect=affect_config),
            llm,
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        # tanpa mention/reply ke bot: model tidak ditanya, emosi tidak bergerak
        await handle_message(client, group_event("dasar bot sampah sanka", mention=False), app)
        return sent, presence

    sent, presence = asyncio.run(flow())

    assert sent == [] and presence == []
    assert llm.decisions == []
    assert store.status("1234567890@g.us", "628222222222")["pleasure"] == 0.0
    store.close()


def test_crisis_bypasses_decision(tmp_path) -> None:
    llm = DecisionLLM(answer="aku di sini, kamu nggak sendirian")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("aku pengen mati aja rasanya"), app)
        return sent

    sent = asyncio.run(flow())

    assert llm.decisions == []  # krisis tidak memanggil LLM keputusan
    assert "Pengirim sedang dalam krisis" in llm.system
    assert [payload_text(item) for item in sent] == ["aku di sini, kamu nggak sendirian"]


def test_professional_decision_mode_uses_report(tmp_path) -> None:
    report = "hasil cek server\n" + "\n".join(f"• baris {i} " + "data " * 20 for i in range(8))
    llm = DecisionLLM(
        answer=report, decision=verdict(mode="professional", reason="tugas kerja")
    )

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("cek server dong"), app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 1
    assert payload_text(sent[0]) == report.strip()
    assert "Mode profesional" in llm.system


def test_daily_decision_with_tool_does_not_force_report(tmp_path) -> None:
    report = "laporan\n" + "\n".join(f"• item {i} " + "angka " * 20 for i in range(8))

    class ToolLLM(DecisionLLM):
        async def run(self, system, user_content, specs, ctx, session="") -> str:
            ctx.used.append("shell")
            return report

    llm = ToolLLM(decision=verdict("reply", mode="daily", addressed="me"))

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == [report.strip()]  # daily: satu pesan, isi utuh
    assert "Mode profesional" not in llm.system  # tapi bukan format laporan profesional


def test_mention_gets_cold_reply_when_emotion_negative(tmp_path) -> None:
    store, affect_config = affect_setup(tmp_path)
    llm = DecisionLLM(
        answer="ya udah",
        decision=verdict("reply", tone="curt", label="hostility", dP=-0.4, note="dihina"),
    )

    async def flow():
        client, sent, _ = transport()
        app = App(
            rich_config(tmp_path, affect=affect_config),
            llm,
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        # hinaan sebelumnya sudah membuat state emosi negatif
        store.turn_start(GROUP, "OLD")
        store.turn_end(
            GROUP,
            "628222222222",
            "Fahri",
            "kamu culun",
            "member",
            "628222222222@s.whatsapp.net",
            Decision(
                addressed="me",
                act="reply",
                mode="daily",
                tone="hurt",
                bubbles=1,
                appraisal=Appraisal("hostility", -0.4, 0.0, 0.0, "dihina"),
                relationship=RelationshipDelta(annoyance=0.15),
                reason="hinaan",
            ),
            "OLD",
        )
        await handle_message(client, group_event("@sanka halo"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["ya udah"]
    assert "Nada: curt" in llm.system
    assert "Nada curt: maksimal satu kalimat pendek" in llm.system
    assert "guarded" in llm.system  # blok emosi ikut masuk prompt balasan
    assert store.status(GROUP, "628222222222")["pleasure"] < 0
    store.close()


def test_burst_of_mentions_replies_once(tmp_path) -> None:
    import time as time_module

    class SlowLLM(DecisionLLM):
        async def complete(self, system, user, *, max_tokens=300, temperature=0.2, session="") -> str:
            await asyncio.sleep(0)  # beri kesempatan event lain masuk sebelum burst dikirim
            self.decisions.append(user)
            return self.decision

    async def flow():
        client, sent, _ = transport()
        llm = SlowLLM()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        old = int(time_module.time()) - 30
        events = []
        for index in range(3):
            ev = group_event("sanka halo", event_id=f"3EB0BURST{index}")
            ev.Info.Timestamp = old + index
            events.append(ev)
        await asyncio.gather(*(handle_message(client, ev, app) for ev in events))
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["siap"]  # satu burst untuk tag beruntun


def test_command_does_not_call_decision_or_touch_emotion(tmp_path) -> None:
    store, affect_config = affect_setup(tmp_path)
    llm = DecisionLLM()

    async def flow():
        client, sent, _ = transport()
        app = App(
            rich_config(tmp_path, affect=affect_config),
            llm,
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/tools"), app)
        return sent

    sent = asyncio.run(flow())

    assert llm.decisions == []  # command tidak memakai LLM keputusan
    assert payload_text(sent[0])  # command tetap dibalas
    assert store.stats()["states"] == 0  # command tidak membuat state emosi
    store.close()


def test_group_command_runs_without_mention(tmp_path) -> None:
    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), DecisionLLM(), FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/help", mention=False), app)
        return sent

    sent = asyncio.run(flow())

    assert payload_text(sent[0]).startswith("🧭 *Command Sanka*")


def test_calmdown_command_repairs_emotion_for_owner_only(tmp_path) -> None:
    roles = {
        "owner": RoleConfig("owner", users=("628222222222",), tools=("*",), commands=("*",)),
        "member": RoleConfig("member", tools=("read",), commands=("help",)),
    }

    async def flow():
        client, sent, _ = transport()
        store, affect_config = affect_setup(tmp_path)
        store.turn_start("1234567890@g.us", "MSG1")
        store.turn_end(
            "1234567890@g.us",
            "628222222222",
            "Muqsith",
            "goblok",
            "owner",
            "628222222222@s.whatsapp.net",
            Decision(
                addressed="me",
                act="reply",
                mode="daily",
                tone="hurt",
                bubbles=1,
                appraisal=Appraisal("hostility", -0.4, 0.0, 0.0, "dihina"),
                relationship=RelationshipDelta(trust=-0.1, annoyance=0.15),
                reason="hinaan",
            ),
            "MSG1",
        )
        app = App(
            rich_config(
                tmp_path,
                affect=affect_config,
                roles=roles,
                default_role="member",
            ),
            FakeLLM(),
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        hurt_pleasure = store.status("1234567890@g.us")["pleasure"]
        await handle_message(client, group_event("/calmdown"), app)
        return sent, store, hurt_pleasure

    sent, store, hurt_pleasure = asyncio.run(flow())

    assert payload_text(sent[0]) == "okeee... aku udah agak tenang 😌 makasih ya."
    assert store.status("1234567890@g.us")["pleasure"] > hurt_pleasure
    store.close()


def test_calmdown_command_denied_for_member(tmp_path) -> None:
    roles = {
        "owner": RoleConfig("owner", users=("628222222222",), tools=("*",), commands=("*",)),
        "member": RoleConfig("member", tools=("read",), commands=("help",)),
    }

    async def flow():
        client, sent, _ = transport()
        store, affect_config = affect_setup(tmp_path)
        app = App(
            rich_config(
                tmp_path,
                affect=affect_config,
                roles=roles,
                default_role="member",
            ),
            FakeLLM(),
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        ev = group_event("/calmdown")
        ev.Info.MessageSource.Sender.User = "628999999999"
        await handle_message(client, ev, app)
        return sent, store

    sent, store = asyncio.run(flow())

    assert payload_text(sent[0]).startswith("🔒 Command /calmdown tidak diizinkan")
    store.close()


def test_system_prompt_places_emotion_style_topic_and_safety() -> None:
    from sanka.wa import system_prompt

    prompt = system_prompt(
        make_config(),
        "member",
        {"628567770837"},
        memory="Memori tersimpan:\n- suka kopi",
        actions="Aksi lintas chat terakhir:\n- kirim stiker",
        skills="Skill lokal:\n- deploy: Deploy aman.",
        topic="Topik yang sedang hangat: rapat",
        emotion="Keadaan hatimu: guarded",
        style="Gaya chat manusia di sini",
        safety="Pengirim sedang dalam krisis",
        tone="curt",
        decision_reason="dihina",
    )

    order = [
        prompt.index("Topik yang sedang hangat"),
        prompt.index("Keadaan hatimu: guarded"),
        prompt.index("Gaya chat manusia di sini"),
        prompt.index("Pengirim sedang dalam krisis"),
        prompt.index("suka kopi"),
        prompt.index("Aksi lintas chat terakhir"),
        prompt.index("Skill lokal"),
    ]
    assert order == sorted(order)
    assert "Keputusanmu: dihina. Nada: curt." in prompt


def test_prompt_budget_drops_optional_blocks() -> None:
    from dataclasses import replace

    from sanka.wa import system_prompt

    config = make_config()
    blocks = {"memory": "x" * 6000, "topic": "topik" * 240}
    base_len = len(system_prompt(config, "member", None))
    # Anggaran muat base + topik tapi tidak + memori: salience membuang yang tak relevan.
    tight = replace(config, prompt=replace(config.prompt, max_chars=base_len + len(blocks["topic"]) + 10))

    prompt = system_prompt(tight, "member", None, query="kopi", bonuses={"topic": 0.3}, **blocks)

    assert "x" * 6000 not in prompt  # memori tanpa irisan kata kunci dibuang
    assert "topik" * 240 in prompt
    roomy = replace(config, prompt=replace(config.prompt, max_chars=50000))
    full = system_prompt(roomy, "member", None, query="kopi", bonuses={"topic": 0.3}, **blocks)
    assert "x" * 6000 in full and "topik" * 240 in full


def test_system_prompt_does_not_claim_bot_assistant() -> None:
    from sanka.wa import system_prompt

    prompt = system_prompt(make_config(), "member", {"628567770837"})

    assert "bot WhatsApp" not in prompt
    assert "Balas seperti chat WhatsApp sungguhan" in prompt
    assert "jangan mengaku AI" in prompt
    assert "Kamu yang memutuskan" in prompt
    assert "Runtime sudah menyaring" not in prompt


def test_person_style_is_in_prompt(tmp_path) -> None:
    from sanka.config import StyleConfig
    from sanka.style import StyleStore

    llm = DecisionLLM()
    style = StyleStore(tmp_path / "style.json", StyleConfig(enabled=True))
    for _ in range(10):
        style.learn("iyaa kok gitu sih wkwk", person_id="628222222222")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions(), style=style)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("halo sanka"), app)
        return sent

    asyncio.run(flow())

    assert "Cara chat orang ini" in llm.system
    assert "Pengirim sekarang: " in llm.system
    style.close()


def command_reply(tmp_path: Path, text: str, is_dm: bool = False) -> str:
    """Satu turn dengan semua command diizinkan untuk member; balasan pertama dikembalikan."""
    roles = {"member": RoleConfig("member", tools=("read",), commands=("*",))}

    async def flow():
        client, sent, _ = transport()
        app = App(
            rich_config(tmp_path, roles=roles),
            DecisionLLM(),
            FakeHub(),
            Sessions(),
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event(text) if is_dm else group_event(text), app)
        return sent

    sent = asyncio.run(flow())
    assert len(sent) == 1
    return payload_text(sent[0])


def test_mentioned_command_runs_handler_instead_of_llm(tmp_path) -> None:
    assert command_reply(tmp_path, "@Sanka /ping") == "🏓 Pong! Sanka hidup dan siap."


def test_numeric_mention_before_command_runs_handler(tmp_path) -> None:
    assert command_reply(tmp_path, "@628567770837 /ping") == "🏓 Pong! Sanka hidup dan siap."


def test_mentioned_command_in_dm_reaches_handler(tmp_path) -> None:
    assert command_reply(tmp_path, "@Sanka /mood", is_dm=True) == "state emosi belum aktif."


def test_command_for_someone_else_is_not_a_command(tmp_path) -> None:
    text = command_reply(tmp_path, "@Ilham /ping")

    assert text != "🏓 Pong! Sanka hidup dan siap."
    assert "Command" not in text


def test_mentioned_mood_returns_card_not_persona_reply(tmp_path) -> None:
    store, affect_config = affect_setup(tmp_path)

    async def flow():
        client, sent, _ = transport()
        app = App(
            rich_config(
                tmp_path,
                affect=affect_config,
                roles={"member": RoleConfig("member", tools=("read",), commands=("*",))},
            ),
            DecisionLLM(),
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("@Sanka /mood"), app)
        return sent

    sent = asyncio.run(flow())
    text = payload_text(sent[0])

    assert len(sent) == 1
    assert text.splitlines()[0] == "💭 *Mood Sanka*"
    assert any(line.startswith("• Pulse:") for line in text.splitlines())
    assert any(line.startswith("• Trust:") for line in text.splitlines())
    store.close()


def test_dm_without_mention_or_name_is_answered(tmp_path) -> None:
    llm = DecisionLLM(answer="lagi rebahan")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("lagi apa kamu"), app)
        return sent

    sent = asyncio.run(flow())

    assert llm.calls  # DM tidak butuh tag atau namamu disebut
    assert payload_text(sent[0]) == "lagi rebahan"


def test_backlog_is_archived_not_answered(tmp_path) -> None:
    import time as time_module

    llm = DecisionLLM()
    old = int(time_module.time()) - 3600

    async def flow():
        client, sent, presence = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        for index in range(2):
            ev = dm_event(f"pesan lama {index}")
            ev.Info.ID = f"3EB0OLD{index}"
            ev.Info.Timestamp = old + index
            await handle_message(client, ev, app)
        return sent, presence, app

    sent, presence, app = asyncio.run(flow())

    assert sent == [] and presence == []
    assert llm.decisions == []  # arsip: tanpa keputusan LLM
    assert len(app.sessions.get("628999999999@s.whatsapp.net")) == 2  # tetap jadi konteks
    assert app.backlog["628999999999@s.whatsapp.net"] == 2


def test_backlog_gap_is_named_once(tmp_path) -> None:
    import time as time_module

    llm = DecisionLLM(answer="halo juga")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        old = dm_event("ketinggalan satu jam")
        old.Info.ID = "3EB0GAP0"
        old.Info.Timestamp = int(time_module.time()) - 3600
        await handle_message(client, old, app)
        fresh = dm_event("halo sanka")
        fresh.Info.ID = "3EB0GAP1"
        await handle_message(client, fresh, app)
        first_system, first_decision = llm.system, llm.decisions[0]
        second = dm_event("masih di situ?")
        second.Info.ID = "3EB0GAP2"
        await handle_message(client, second, app)
        return sent, app, first_system, first_decision

    sent, app, first_system, first_decision = asyncio.run(flow())

    assert len(sent) == 2
    assert "baru aktif lagi" in first_system and "1 pesan" in first_system
    assert "baru aktif lagi" in first_decision
    assert "baru aktif lagi" not in llm.system  # catatan tidak diulang di turn berikutnya
    assert app.backlog == {}


def test_stop_silences_chat_until_reset(tmp_path) -> None:
    llm = DecisionLLM(answer="iya")

    async def flow():
        client, sent, _ = transport()
        app = App(
            rich_config(
                tmp_path,
                roles={"owner": RoleConfig("owner", commands=("*",))},
                default_role="owner",
            ),
            llm,
            FakeHub(),
            Sessions(),
        )
        app.bot_digits = {"628567770837"}

        async def say(text: str, event_id: str) -> None:
            ev = dm_event(text)
            ev.Info.ID = event_id
            await handle_message(client, ev, app)

        await say("/stop", "3EB0STOP0")
        await say("halo sanka", "3EB0STOP1")
        await say("/help", "3EB0STOP2")
        await say("/stop 0", "3EB0STOP3")
        await say("halo lagi", "3EB0STOP4")
        return sent, app

    sent, app = asyncio.run(flow())
    texts = [payload_text(item) for item in sent]

    assert texts[0] == "ok, aku diam 10 menit. Panggil /stop 0 kalau mau aku aktif lagi."
    assert texts[1].startswith("🧭 *Command Sanka*")  # command tetap jalan saat diam
    assert texts[2:] == ["ok, aku aktif lagi.", "iya"]
    assert len(llm.decisions) == 1  # quiet: satu-satunya keputusan LLM dari DM terakhir
    assert app.quiet == {}


def test_message_arriving_during_reply_gets_its_own_turn(tmp_path) -> None:
    class SlowLLM(DecisionLLM):
        def __init__(self) -> None:
            super().__init__(answer="siap")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.started.set()
            await self.release.wait()
            return await super().run(system, user_content, specs, ctx, session)

    async def flow():
        client, sent, _ = transport()
        llm = SlowLLM()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        first = dm_event("halo sanka")
        first.Info.ID = "3EB0DURING0"
        task = asyncio.create_task(handle_message(client, first, app))
        await asyncio.wait_for(llm.started.wait(), timeout=5)
        second = dm_event("eh masih di situ?")
        second.Info.ID = "3EB0DURING1"
        other = asyncio.create_task(handle_message(client, second, app))
        llm.release.set()
        await asyncio.gather(task, other)
        return sent, llm

    sent, llm = asyncio.run(flow())

    assert len(sent) == 2  # pesan yang masuk saat LLM kerja tidak dibuang diam-diam
    assert len(llm.calls) == 2
    assert "eh masih di situ?" not in llm.calls[0]  # payload turn pertama tidak mengklaimnya
    assert "eh masih di situ?" in llm.calls[1]


def test_reflection_stores_mood_with_memory(tmp_path) -> None:
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig
    from sanka.memory import MemoryStore

    class FactLLM(ReflectLLM):
        async def reflect(self, user_text, answer, context="", memory=""):
            self.reflected += 1
            return [{"text": "suka kopi pahit", "kind": "preference"}]

    affect = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
    memory = MemoryStore(tmp_path / "memory.db")
    llm = FactLLM()
    config = rich_config(
        tmp_path,
        roles={"owner": RoleConfig("owner", tools=("memory",), commands=("*",))},
        default_role="owner",
    )
    app = App(config, llm, FakeHub(), Sessions(), memory=memory, affect=affect)
    app.bot_digits = {"628567770837"}
    client, _, _ = transport()
    ev = dm_event("aku suka kopi pahit banget")
    ev.Info.ID = "3EB0PAD"

    async def flow() -> None:
        await handle_message(client, ev, app)
        await asyncio.gather(*app.tasks)

    asyncio.run(flow())

    pad = affect.pad("628999999999@s.whatsapp.net")
    row = memory.entries(["628999999999@s.whatsapp.net"])[0]
    assert llm.reflected == 1
    assert row["valence"] == pad["pleasure"]  # mood saat menulis ikut tersimpan
    assert row["arousal"] == pad["arousal"]
    memory.close()
    affect.close()


def test_room_vibe_reaches_decision_and_reply(tmp_path) -> None:
    BLOCK = "Vibe ruangan: negatif, berenergi, seimbang."

    class FakeVibe:
        def __init__(self) -> None:
            self.seen: list[tuple[str, str]] = []
            self.closed = 0

        async def observe(self, chat_id: str, text: str) -> str:
            self.seen.append((chat_id, text))
            return BLOCK

        async def close(self) -> None:
            self.closed += 1

    llm = DecisionLLM(answer="santai aja")
    vibe = FakeVibe()

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions(), anima=vibe)
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("aku capek banget"), app)
        await app.close()
        return sent

    sent = asyncio.run(flow())

    assert vibe.seen == [("628999999999@s.whatsapp.net", "aku capek banget")]
    assert BLOCK in llm.decisions[0] and BLOCK in llm.system
    assert vibe.closed == 1
    assert payload_text(sent[0]) == "santai aja"


def test_group_captionless_image_is_ignored(tmp_path) -> None:
    llm = DecisionLLM(answer="aku nongol lagi")

    def image_event() -> Message:
        ev = Message()
        ev.Info.ID = "3EB0IMG"
        ev.Info.MessageSource.Chat.User = "1234567890"
        ev.Info.MessageSource.Chat.Server = "g.us"
        ev.Info.MessageSource.Sender.User = "628222222222"
        ev.Info.MessageSource.Sender.Server = "s.whatsapp.net"
        ev.Message.imageMessage.mimetype = "image/jpeg"
        ev.Message.imageMessage.fileLength = 1024
        return ev

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, image_event(), app)
        return sent

    sent = asyncio.run(flow())

    assert sent == []  # gambar tanpa caption bukan panggilan
    assert llm.decisions == [] and llm.calls == []


def test_group_name_without_tag_is_ignored(tmp_path) -> None:
    llm = DecisionLLM(answer="lagi di sini")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("sanka lagi apa?", mention=False), app)
        return sent

    sent = asyncio.run(flow())

    assert sent == []
    assert llm.decisions == []
    assert llm.calls == []


def test_cold_tone_is_softened_when_mood_is_calm(tmp_path) -> None:
    store, affect_config = affect_setup(tmp_path)
    llm = DecisionLLM(answer="iya", decision=verdict("reply", tone="hurt"))

    async def flow():
        client, _, _ = transport()
        app = App(
            rich_config(tmp_path, affect=affect_config),
            llm,
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return llm

    llm = asyncio.run(flow())

    assert "Nada: neutral." in llm.system  # mood tenang: tidak mengaku ngambek
    store.close()


def test_cold_tone_survives_when_emotion_is_negative(tmp_path) -> None:
    store, affect_config = affect_setup(tmp_path)
    store.turn_end(
        "628999999999@s.whatsapp.net",
        "628999999999",
        "Muqsith",
        "dasar goblok",
        "member",
        "628999999999@s.whatsapp.net",
        Decision(
            "me", "reply", "daily", "angry", 1, Appraisal("hostility", -0.4, 0.3, 0.1),
            RelationshipDelta(), "kesal",
        ),
        "MSGPRIOR",
    )
    llm = DecisionLLM(answer="iya", decision=verdict("reply", tone="hurt"))

    async def flow():
        client, _, _ = transport()
        app = App(
            rich_config(tmp_path, affect=affect_config),
            llm,
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        event = dm_event("halo sanka")
        event.Info.ID = "3EB0TONED"
        await handle_message(client, event, app)
        return llm

    llm = asyncio.run(flow())

    assert "Nada: hurt." in llm.system  # perasaannya memang negatif
    store.close()


def test_turns_are_queued_across_chats(tmp_path) -> None:
    class GateLLM(DecisionLLM):
        def __init__(self) -> None:
            super().__init__(answer="siap")
            self.started = asyncio.Event()
            self.release = asyncio.Event()
            self.runs = 0

        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.runs += 1
            self.started.set()
            await self.release.wait()
            return await super().run(system, user_content, specs, ctx, session)

    async def flow():
        client, sent, _ = transport()
        llm = GateLLM()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        first = asyncio.create_task(handle_message(client, dm_event("halo sanka"), app))
        await asyncio.wait_for(llm.started.wait(), 5)
        second_ev = group_event("@sanka tolong cek", event_id="3EB0QUEUE1")
        second = asyncio.create_task(handle_message(client, second_ev, app))
        await asyncio.sleep(0.05)
        queued_sends = list(sent)
        queued_runs = llm.runs
        llm.release.set()
        await asyncio.gather(first, second)
        return sent, queued_sends, queued_runs

    sent, queued_sends, queued_runs = asyncio.run(flow())

    assert queued_sends == []  # chat kedua menunggu, bukan jalan bersamaan
    assert queued_runs == 1  # turn kedua belum sampai LLM
    assert len(sent) == 2
    assert Jid2String(sent[0][0]) == "628999999999@s.whatsapp.net"
    assert Jid2String(sent[1][0]) == "1234567890@g.us"


def test_command_does_not_wait_in_the_queue(tmp_path) -> None:
    class GateLLM(DecisionLLM):
        def __init__(self) -> None:
            super().__init__(answer="siap")
            self.started = asyncio.Event()
            self.release = asyncio.Event()

        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.started.set()
            await self.release.wait()
            return await super().run(system, user_content, specs, ctx, session)

    async def flow():
        client, sent, _ = transport()
        llm = GateLLM()
        app = App(
            rich_config(
                tmp_path,
                roles={"owner": RoleConfig("owner", commands=("*",))},
                default_role="owner",
            ),
            llm,
            FakeHub(),
            Sessions(),
        )
        app.bot_digits = {"628567770837"}
        busy = asyncio.create_task(handle_message(client, dm_event("halo sanka"), app))
        await asyncio.wait_for(llm.started.wait(), 5)
        stop_ev = dm_event("/stop")
        stop_ev.Info.ID = "3EB0STOPQ"
        await asyncio.wait_for(handle_message(client, stop_ev, app), 5)  # tidak ikut antre
        llm.release.set()
        await busy
        return sent, app

    sent, app = asyncio.run(flow())

    assert "aku diam" in payload_text(sent[0])  # /stop langsung dijawab
    assert app.quiet


def test_group_followup_without_mention_is_ignored(tmp_path) -> None:
    llm = DecisionLLM(answer="rapatnya jam tiga")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(
            client, group_event("@sanka catatan rapat jam tiga", event_id="3EB0F1"), app
        )
        await handle_message(
            client, group_event("rapat jam tiga di mana?", mention=False, event_id="3EB0F2"), app
        )
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 1  # lanjutan tanpa mention: balasan tidak bertambah
    assert len(llm.decisions) == 1


def test_group_reply_to_bot_without_mention_is_answered(tmp_path) -> None:
    llm = DecisionLLM(answer="iya, ini dia")

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        ev = group_event("iya, tolong", mention=False)
        context = ev.Message.extendedTextMessage.contextInfo
        context.participant = BOT_JID
        context.stanzaID = "3EB0BOTMSG"
        context.quotedMessage.conversation = "mau kubantu?"
        await handle_message(client, ev, app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 1  # reply ke pesan bot tanpa mention tetap dijawab


def recipe_app(tmp_path, roles: dict, workspace_files: dict | None = None) -> App:
    from sanka.recipes import RecipeStore

    config = Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path / "workspace",
        whatsapp=WhatsAppConfig(session_db=tmp_path / "wa.db"),
        roles=roles,
        default_role="member",
        sandbox=SandboxConfig(),
        mcp={},
    )
    if workspace_files:
        (tmp_path / "workspace").mkdir(parents=True, exist_ok=True)
        for name, body in workspace_files.items():
            (tmp_path / "workspace" / name).write_text(body, encoding="utf-8")
    app = App(config, FakeLLM(), FakeHub(), Sessions(), recipes=RecipeStore(tmp_path / "recipes"))
    app.bot_digits = {"628567770837"}
    return app


def sent_text(sent: list[tuple]) -> str:
    return sent[0][1].extendedTextMessage.text


def test_recipe_command_reads_file(tmp_path) -> None:
    from sanka.config import RoleConfig as RC

    app = recipe_app(
        tmp_path,
        {"member": RC("member", tools=("read",), commands=("*",))},
        {"a.txt": "isi rekap divisi"},
    )
    app.recipes.save(
        "rekap", "recipe", "Rekap.", steps=[{"tool": "read", "args": {"path": "a.txt"}}]
    )

    client, sent, _ = transport()
    asyncio.run(handle_message(client, group_event("/rekap divisi"), app))

    assert len(sent) == 1
    assert "isi rekap divisi" in sent_text(sent)


def test_recipe_command_owner_only_locked_for_member(tmp_path) -> None:
    from sanka.config import RoleConfig as RC

    app = recipe_app(
        tmp_path,
        {"member": RC("member", tools=("*",), commands=("*",))},
        {"a.txt": "rahasia"},
    )
    app.recipes.save(
        "rekap",
        "recipe",
        "Rekap.",
        roles=["owner"],
        steps=[{"tool": "read", "args": {"path": "a.txt"}}],
    )

    client, sent, _ = transport()
    asyncio.run(handle_message(client, group_event("/rekap"), app))

    assert sent_text(sent) == "🔒 Command /rekap tidak diizinkan untuk role member."


def test_prompt_recipe_delegates_one_turn(tmp_path) -> None:
    from sanka.commands import REGISTRY as COMMANDS
    from sanka.config import RoleConfig as RC

    app = recipe_app(tmp_path, {"member": RC("member", tools=("*",), commands=("*",))})
    app.recipes.save("puisi", "prompt", "Bikin puisi.", prompt="Buatkan puisi tentang {{args}}.")

    client, sent, _ = transport()
    asyncio.run(handle_message(client, group_event("/puisi senja"), app))

    assert sent_text(sent) == "siap"
    assert COMMANDS.get("puisi") is None
    assert COMMANDS.get("rekap") is None
