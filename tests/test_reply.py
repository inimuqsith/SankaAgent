"""Kontrak balasan: quote wajib wrapper event Neonize, target wajib chat asal."""

from __future__ import annotations

import asyncio
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


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list = []

    async def run(self, system, user_content, specs, ctx, session="") -> str:
        self.calls.append(user_content)
        return "siap"


class CaptureLLM:
    def __init__(self) -> None:
        self.system = ""
        self.ctx = None

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


# --- emosi, gate, bubble pendek, dan double-text ---------------------------------


def rich_config(
    tmp_path: Path,
    *,
    humanize=None,
    gate=None,
    affect=None,
    roles: dict | None = None,
    default_role: str = "member",
) -> Config:
    from sanka.config import AffectConfig, GateConfig, HumanizeConfig

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
        gate=gate or GateConfig(enabled=False),
        humanize=humanize
        or HumanizeConfig(enabled=True, min_delay=0, max_delay=0, between_delay=0, followup_delay=0),
    )


def long_text(words: int = 120) -> str:
    return " ".join(["kata"] * words)


def payload_text(item) -> str:
    payload = item[1]
    return payload if isinstance(payload, str) else payload.extendedTextMessage.text


def test_long_answer_splits_into_short_bubbles(tmp_path) -> None:
    class LongLLM(FakeLLM):
        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.calls.append(user_content)
            return long_text()

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), LongLLM(), FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 3
    assert all(len(payload_text(item)) <= 220 for item in sent)
    assert sent[0][1].extendedTextMessage.contextInfo.stanzaID == "3EB0DM"
    assert isinstance(sent[1][1], str) and isinstance(sent[2][1], str)


class FollowLLM(FakeLLM):
    def __init__(self, answer: str = "ih apasih sok asik banget") -> None:
        super().__init__()
        self.answer = answer
        self.followups = 0

    async def run(self, system, user_content, specs, ctx, session="") -> str:
        self.calls.append(user_content)
        return self.answer

    async def followup(self, system, last_user, sent) -> str:
        self.followups += 1
        return "lagian kamu itu..." if len(sent) < 2 else "[DIAM]"


def test_followup_adds_messages_until_diam(tmp_path) -> None:
    from sanka.config import HumanizeConfig

    async def flow():
        client, sent, _ = transport()
        llm = FollowLLM()
        config = rich_config(
            tmp_path,
            humanize=HumanizeConfig(
                enabled=True,
                followups=True,
                max_followups=2,
                max_bubbles=4,
                min_delay=0,
                max_delay=0,
                between_delay=0,
                followup_delay=0,
            ),
        )
        app = App(config, llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("halooo sanka"), app)
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == [
        "ih apasih sok asik banget",
        "lagian kamu itu...",
    ]
    assert sent[0][1].extendedTextMessage.contextInfo.stanzaID == "3EB0GROUP"
    assert isinstance(sent[1][1], str)
    assert llm.followups == 2  # percobaan kedua dijawab [DIAM]


def test_followup_respects_max_bubbles(tmp_path) -> None:
    from sanka.config import HumanizeConfig

    class AlwaysFollow(FollowLLM):
        async def followup(self, system, last_user, sent) -> str:
            self.followups += 1
            return f"tambahan {self.followups}"

    async def flow():
        client, sent, _ = transport()
        llm = AlwaysFollow(answer=long_text())
        config = rich_config(
            tmp_path,
            humanize=HumanizeConfig(
                enabled=True,
                followups=True,
                max_followups=5,
                max_bubbles=4,
                normal_parts=3,
                min_delay=0,
                max_delay=0,
                between_delay=0,
                followup_delay=0,
            ),
        )
        app = App(config, llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("halooo sanka"), app)
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert len(sent) == 4  # 3 bubble awal + 1 lanjutan, total = max_bubbles
    assert llm.followups == 1


def test_followup_skips_command(tmp_path) -> None:
    from sanka.config import HumanizeConfig

    async def flow():
        client, sent, _ = transport()
        llm = FollowLLM()
        config = rich_config(
            tmp_path,
            humanize=HumanizeConfig(enabled=True, followups=True, followup_delay=0),
        )
        app = App(config, llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/help"), app)
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert sent  # /help menjawab
    assert llm.followups == 0


def test_followup_stops_when_sulking(tmp_path) -> None:
    from sanka import wa as wa_module

    class StubAffect:
        def silent(self, chat_id, person_id="") -> bool:
            return True

    class StubLLM:
        def __init__(self) -> None:
            self.followups = 0

        async def followup(self, system, last_user, sent) -> str:
            self.followups += 1
            return "harusnya tidak dipakai"

    async def flow():
        client, sent, _ = transport()
        llm = StubLLM()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions(), affect=StubAffect())
        app.bot_digits = {"628567770837"}
        await wa_module._followups(client, app, "c@g.us", "628222222222", "halo", ["satu"], "", "")
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert sent == []
    assert llm.followups == 0


def test_llm_decision_can_ignore_even_mention(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    class DecidingLLM(FakeLLM):
        async def decide(self, system, user) -> str:
            return '{"act":"ignore","why":"bukan buat aku"}'

    async def flow():
        client, sent, _ = transport()
        llm = DecidingLLM()
        config = rich_config(
            tmp_path, gate=GateConfig(enabled=True, llm_decide=True, reply_cooldown_seconds=0)
        )
        gate = ReplyGate(config.gate, llm, None, Sessions(), "sanka", ())
        app = App(config, llm, FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("@sanka halo"), app)
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert sent == []
    assert llm.calls == []


def test_sulking_refuses_command_without_tool(tmp_path) -> None:
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig

    async def flow():
        client, sent, _ = transport()
        llm = FakeLLM()
        store = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
        app = App(
            rich_config(tmp_path, affect=AffectConfig(enabled=True)),
            llm,
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("goblok banget sih kamu"), app)
        assert store.silent("628999999999@s.whatsapp.net")
        second = dm_event("tolong rekap absen")
        second.Info.ID = "3EB0DM2"
        await handle_message(client, second, app)
        return llm, sent, store

    llm, sent, store = asyncio.run(flow())

    assert llm.calls == []
    assert sent == []
    store.close()


def test_calmdown_command_clears_for_owner_only(tmp_path) -> None:
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig

    roles = {
        "owner": RoleConfig("owner", users=("628222222222",), tools=("*",), commands=("*",)),
        "member": RoleConfig("member", tools=("read",), commands=("help",)),
    }

    async def flow():
        client, sent, _ = transport()
        store = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
        store.observe(
            "1234567890@g.us",
            "628222222222",
            "Muqsith",
            "goblok",
            directed=True,
            is_dm=False,
            role="owner",
        )
        app = App(
            rich_config(
                tmp_path,
                affect=AffectConfig(enabled=True),
                roles=roles,
                default_role="member",
            ),
            FakeLLM(),
            FakeHub(),
            Sessions(),
            affect=store,
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/calmdown"), app)
        return sent, store

    sent, store = asyncio.run(flow())

    assert payload_text(sent[0]) == "okeee... aku udah agak tenang 😌 makasih ya."
    assert store.silent("1234567890@g.us") is False
    store.close()


def test_calmdown_command_denied_for_member(tmp_path) -> None:
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig

    roles = {
        "owner": RoleConfig("owner", users=("628222222222",), tools=("*",), commands=("*",)),
        "member": RoleConfig("member", tools=("read",), commands=("help",)),
    }

    async def flow():
        client, sent, _ = transport()
        store = AffectStore(tmp_path / "affect.db", AffectConfig(enabled=True))
        app = App(
            rich_config(
                tmp_path,
                affect=AffectConfig(enabled=True),
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


def test_system_prompt_places_affect_style_topic_and_safety() -> None:
    from sanka.wa import system_prompt

    prompt = system_prompt(
        make_config(),
        "member",
        {"628567770837"},
        memory="Memori tersimpan:\n- suka kopi",
        actions="Aksi lintas chat terakhir:\n- kirim stiker",
        skills="Skill lokal:\n- deploy: Deploy aman.",
        topic="Topik yang sedang hangat: rapat",
        affect="Keadaan hatimu sekarang: ngambek",
        style="Gaya chat manusia di sini",
        safety="Pengirim sedang dalam krisis",
    )

    order = [
        prompt.index("Topik yang sedang hangat"),
        prompt.index("Keadaan hatimu sekarang"),
        prompt.index("Gaya chat manusia di sini"),
        prompt.index("Pengirim sedang dalam krisis"),
        prompt.index("suka kopi"),
        prompt.index("Aksi lintas chat terakhir"),
        prompt.index("Skill lokal"),
    ]
    assert order == sorted(order)


def test_system_prompt_does_not_claim_bot_assistant() -> None:
    from sanka.wa import system_prompt

    prompt = system_prompt(make_config(), "member", {"628567770837"})

    assert "bot WhatsApp" not in prompt
    assert "Balas 1-3 pesan pendek" in prompt
    assert "jangan mengaku AI" in prompt


def test_command_does_not_consume_reply_budget(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    async def flow():
        client, _, _ = transport()
        config = rich_config(tmp_path, gate=GateConfig(enabled=True, reply_cooldown_seconds=45))
        gate = ReplyGate(config.gate, None, None, Sessions(), "sanka", ())
        app = App(config, FakeLLM(), FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/help"), app)
        return gate, app

    gate, app = asyncio.run(flow())

    assert gate._sent == {}
    # pesan biasa sesudah command tetap boleh dibalas (tanpa cooldown dari command)
    second = asyncio.run(
        gate.decide(
            chat_id="1234567890@g.us",
            text="halo sanka",
            is_dm=False,
            mentioned=True,
            replied_to_bot=False,
            sender_id="628222222222",
            has_media=False,
            quote_sender_is_bot=False,
        )
    )
    assert second.act == "reply"
