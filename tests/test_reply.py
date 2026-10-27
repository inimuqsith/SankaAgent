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
    require_mention: bool = True,
) -> Config:
    from sanka.config import AffectConfig, GateConfig, HumanizeConfig

    return Config(
        persona="p",
        llm=LLMConfig(base_url="http://x", model="m", api_key_env="K"),
        workspace=tmp_path,
        whatsapp=WhatsAppConfig(
            session_db=tmp_path / "wa.db", require_mention=require_mention
        ),
        roles=roles or {"member": RoleConfig("member", tools=("read",), commands=("help",))},
        default_role=default_role,
        sandbox=SandboxConfig(),
        mcp={},
        affect=affect or AffectConfig(enabled=False),
        gate=gate or GateConfig(enabled=False),
        humanize=humanize
        or HumanizeConfig(enabled=True, normal_parts=2, min_delay=0, max_delay=0, between_delay=0),
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
            return long_text(70)

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), LongLLM(), FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 2
    assert all(len(payload_text(item)) <= 220 for item in sent)
    assert sent[0][1].extendedTextMessage.contextInfo.stanzaID == "3EB0DM"
    assert isinstance(sent[1][1], str)


class AppraiseLLM(FakeLLM):
    """LLM tiruan: appraisal emosi tetap, balasan tetap, dan system prompt terekam."""

    def __init__(self, appraisal: dict | None = None, answer: str = "iya aku dengar") -> None:
        super().__init__()
        self.appraisal = appraisal or {}
        self.answer = answer
        self.system = ""
        self.specs: list[list] = []
        self.appraise_calls = 0

    async def appraise(self, user: str) -> dict:
        self.appraise_calls += 1
        return dict(self.appraisal)

    async def run(self, system, user_content, specs, ctx, session="") -> str:
        self.system = system
        self.specs.append(list(specs))
        self.calls.append(user_content)
        return self.answer


ABUSE = {"intent": "ABUSE", "directed": True, "hurt": 0.7, "warmth": 0.0, "sarcasm": False}
CASUAL = {"intent": "CASUAL", "directed": True, "hurt": 0.0, "warmth": 0.2, "sarcasm": False}


def affect_setup(tmp_path: Path):
    from sanka.affect import AffectStore
    from sanka.config import AffectConfig

    config = AffectConfig(enabled=True)
    return AffectStore(tmp_path / "affect.db", config), config


def test_one_turn_sends_no_extra_bubble(tmp_path) -> None:
    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), FakeLLM(), FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["siap"]


def test_gate_bubbles_cap_chunks(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    class BubbleLLM(AppraiseLLM):
        async def decide(self, system, user) -> str:
            return '{"act":"reply","bubbles":3}'

    async def flow():
        client, sent, _ = transport()
        llm = BubbleLLM(answer="kalimat satu. " * 100)
        config = rich_config(
            tmp_path,
            require_mention=False,
            gate=GateConfig(enabled=True, llm_decide=True),
        )
        gate = ReplyGate(config.gate, llm, None, Sessions(), "sanka", ())
        app = App(config, llm, FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("sanka halo", mention=False), app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 3  # maksimum 3 bubble walau jawaban butuh lebih
    assert all(len(payload_text(item)) <= 420 for item in sent)


def test_professional_mode_sends_full_report(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    report = "hasil cek server\n" + "\n".join(f"• baris {i} " + "data " * 20 for i in range(8))

    class ProLLM(FakeLLM):
        async def decide(self, system, user) -> str:
            return '{"act":"reply","mode":"professional"}'

        async def run(self, system, user_content, specs, ctx, session="") -> str:
            self.calls.append(user_content)
            assert "Mode profesional" in system
            return report

    async def flow():
        client, sent, _ = transport()
        config = rich_config(tmp_path, gate=GateConfig(enabled=True, llm_decide=True))
        llm = ProLLM()
        gate = ReplyGate(config.gate, llm, None, Sessions(), "sanka", ())
        app = App(config, llm, FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 1
    assert payload_text(sent[0]) == report.strip()


def test_work_tool_escalates_format(tmp_path) -> None:
    report = "laporan\n" + "\n".join(f"• item {i} " + "angka " * 20 for i in range(8))

    class ToolLLM(FakeLLM):
        async def run(self, system, user_content, specs, ctx, session="") -> str:
            ctx.used.append("shell")
            return report

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), ToolLLM(), FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("halo sanka"), app)
        return sent

    sent = asyncio.run(flow())

    assert len(sent) == 1 and payload_text(sent[0]) == report.strip()


def test_burst_of_mentions_replies_once(tmp_path) -> None:
    import time as time_module

    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    class SlowDecideLLM(FakeLLM):
        async def decide(self, system, user) -> str:
            await asyncio.sleep(0)  # beri kesempatan event lain masuk sebelum burst dikirim
            self.calls.append(user)
            return '{"act":"reply","bubbles":1}'

    async def flow():
        client, sent, _ = transport()
        llm = SlowDecideLLM()
        config = rich_config(
            tmp_path, gate=GateConfig(enabled=True, llm_decide=True)
        )
        gate = ReplyGate(config.gate, llm, None, Sessions(), "sanka", ())
        app = App(config, llm, FakeHub(), Sessions(), gate=gate)
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


def test_named_group_message_reaches_gate(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    class NamedLLM(FakeLLM):
        async def decide(self, system, user) -> str:
            self.calls.append(user)
            return '{"act":"reply","bubbles":1}'

    async def flow():
        client, sent, _ = transport()
        llm = NamedLLM()
        config = rich_config(
            tmp_path,
            require_mention=False,
            gate=GateConfig(enabled=True, llm_decide=True),
        )
        gate = ReplyGate(config.gate, llm, None, Sessions(), "sanka", ())
        app = App(config, llm, FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("sanka kalau ngantuk tidur aja", mention=False), app)
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert llm.calls and "sanka kalau ngantuk" in llm.calls[0]
    assert [payload_text(item) for item in sent] == ["siap"]


def test_llm_appraisal_makes_insult_sulk(tmp_path) -> None:
    from sanka import wa as wa_module

    store, affect_config = affect_setup(tmp_path)
    llm = AppraiseLLM(ABUSE)

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
        await handle_message(client, dm_event("kamu jelek banget sih"), app)
        return sent

    sent = asyncio.run(flow())

    assert store.silent("628999999999@s.whatsapp.net") is True
    assert wa_module.SULK_PROMPT in llm.system
    assert llm.specs[-1] == []  # tanpa tool saat menolak membantu
    assert [payload_text(item) for item in sent] == ["iya aku dengar"]
    store.close()


def test_llm_appraisal_keeps_normal_chat_available(tmp_path) -> None:
    from sanka import wa as wa_module

    store, affect_config = affect_setup(tmp_path)
    llm = AppraiseLLM(CASUAL)

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
        await handle_message(client, dm_event("halo sanka, kamu gimana?"), app)
        return sent

    sent = asyncio.run(flow())

    assert store.silent("628999999999@s.whatsapp.net") is False
    assert wa_module.SULK_PROMPT not in llm.system
    assert [payload_text(item) for item in sent] == ["iya aku dengar"]
    store.close()


def test_sulk_mention_replies_without_tools(tmp_path) -> None:
    from sanka import wa as wa_module

    store, affect_config = affect_setup(tmp_path)
    store.observe(
        "1234567890@g.us",
        "628222222222",
        "Fahri",
        "kamu culun",
        directed=True,
        is_dm=False,
        appraisal=ABUSE,
    )
    llm = AppraiseLLM(CASUAL)

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
        await handle_message(client, group_event("@sanka tolong rekap absen"), app)
        return sent

    sent = asyncio.run(flow())

    assert llm.specs[-1] == []
    assert wa_module.SULK_PROMPT in llm.system
    assert len(sent) == 1  # satu bubble penolakan, bukan rekap
    store.close()


def test_sulk_command_is_refused(tmp_path) -> None:
    from sanka import wa as wa_module

    store, affect_config = affect_setup(tmp_path)
    store.observe(
        "1234567890@g.us",
        "628222222222",
        "Fahri",
        "kamu culun",
        directed=True,
        is_dm=False,
        appraisal=ABUSE,
    )
    llm = AppraiseLLM(CASUAL)

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

    assert llm.calls == []  # handler command tidak dijalankan
    assert payload_text(sent[0]) in wa_module.SULK_REPLIES
    store.close()


def test_crisis_bypasses_sulk(tmp_path) -> None:
    from sanka import wa as wa_module

    store, affect_config = affect_setup(tmp_path)
    store.observe(
        "628999999999@s.whatsapp.net",
        "628999999999",
        "Fahri",
        "kamu culun",
        directed=True,
        is_dm=True,
        appraisal=ABUSE,
    )
    llm = AppraiseLLM(CASUAL)

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
        await handle_message(client, dm_event("aku pengen mati aja rasanya"), app)
        return sent

    sent = asyncio.run(flow())

    assert "krisis" in llm.system
    assert wa_module.SULK_PROMPT not in llm.system
    assert [payload_text(item) for item in sent] == ["iya aku dengar"]
    assert store.silent("628999999999@s.whatsapp.net") is True
    store.close()


def test_llm_ignore_for_mention_still_replies(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    class DecidingLLM(FakeLLM):
        async def decide(self, system, user) -> str:
            return '{"act":"ignore","why":"bukan buat aku"}'

    async def flow():
        client, sent, _ = transport()
        llm = DecidingLLM()
        config = rich_config(
            tmp_path, gate=GateConfig(enabled=True, llm_decide=True)
        )
        gate = ReplyGate(config.gate, llm, None, Sessions(), "sanka", ())
        app = App(config, llm, FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("@sanka halo"), app)
        return llm, sent

    llm, sent = asyncio.run(flow())

    assert [payload_text(item) for item in sent] == ["siap"]
    assert llm.calls


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
            appraisal={"intent": "ABUSE", "directed": True, "hurt": 0.7, "warmth": 0.0},
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
    assert "Balas seperti chat WhatsApp sungguhan" in prompt
    assert "jangan mengaku AI" in prompt


def test_group_command_needs_open_gate(tmp_path) -> None:
    async def reply(require_mention: bool, event_id: str) -> list:
        client, sent, _ = transport()
        app = App(rich_config(tmp_path, require_mention=require_mention), FakeLLM(), FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/help", mention=False, event_id=event_id), app)
        return sent

    closed = asyncio.run(reply(True, "3EB0CMDCLOSED"))
    opened = asyncio.run(reply(False, "3EB0CMDOPEN"))

    assert closed == []  # require_mention: true -> command grup tanpa tag disaring
    assert payload_text(opened[0]).startswith("🧭 *Command Sanka*")


def test_person_style_is_in_prompt(tmp_path) -> None:
    from sanka.config import StyleConfig
    from sanka.style import StyleStore

    llm = CaptureLLM()
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


def test_command_does_not_block_later_reply(tmp_path) -> None:
    from sanka.config import GateConfig
    from sanka.gate import ReplyGate

    async def flow():
        client, _, _ = transport()
        config = rich_config(tmp_path, gate=GateConfig(enabled=True))
        gate = ReplyGate(config.gate, None, None, Sessions(), "sanka", ())
        app = App(config, FakeLLM(), FakeHub(), Sessions(), gate=gate)
        app.bot_digits = {"628567770837"}
        await handle_message(client, group_event("/help"), app)
        return gate, app

    gate, app = asyncio.run(flow())

    # pesan biasa sesudah command tetap dibalas: command tidak meninggalkan state gate
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


def command_reply(tmp_path: Path, text: str, is_dm: bool = False) -> str:
    """Satu turn dengan semua command diizinkan untuk member; balasan pertama dikembalikan."""
    roles = {"member": RoleConfig("member", tools=("read",), commands=("*",))}

    async def flow():
        client, sent, _ = transport()
        app = App(
            rich_config(tmp_path, roles=roles, require_mention=not is_dm),
            AppraiseLLM(CASUAL),
            FakeHub(),
            Sessions(),
        )
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event(text) if is_dm else group_event(text), app)
        return sent

    sent = asyncio.run(flow())
    assert len(sent) == 1
    return sent[0][1].extendedTextMessage.text


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
            AppraiseLLM(CASUAL),
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
    assert any(line.startswith("• Status:") for line in text.splitlines())
    store.close()


def test_dm_without_mention_or_name_is_answered(tmp_path) -> None:
    llm = AppraiseLLM(CASUAL)

    async def flow():
        client, sent, _ = transport()
        app = App(rich_config(tmp_path), llm, FakeHub(), Sessions())
        app.bot_digits = {"628567770837"}
        await handle_message(client, dm_event("lagi apa kamu"), app)
        return sent

    sent = asyncio.run(flow())

    assert llm.calls  # DM tidak butuh tag atau namamu disebut
    assert payload_text(sent[0]) == "iya aku dengar"
