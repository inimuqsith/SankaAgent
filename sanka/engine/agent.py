"""Agent: orkestrasi satu turn percakapan tanpa transport apa pun."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
import time
from datetime import datetime
from pathlib import Path

from . import guard
from .approvals import ApprovalStore
from .commands import REGISTRY as CORE_COMMANDS
from .commands import CommandContext
from .config import Config
from .context import (
    AUTHOR_BOT,
    ChatMessage,
    WorkingMemory,
    build_user_content,
    clean,
    fallback_content,
    keyword_set,
    prompt_entry,
)
from .decision import (
    Appraisal,
    Decision,
    RelationshipDelta,
    decide,
    ensure_reply,
    ground_tone,
)
from .i18n import reload_catalogs, t
from .inbound import Gateway, GatewayError, Inbound
from .llm import LLM
from .memory import MemoryBackend, MemoryContext, memory_status
from .character import effective_limits, polish, regulate
from .knowledge import select_facts
from .permissions import command_allowed, command_patterns, commands_for, tool_patterns, tools_for
from .privacy import PrivacyService
from .patterns import room_register, select_patterns
from .prompts import CRISIS_SAFETY, away_block, character_block, research_block, system_prompt
from .registry import Handler, Registry
from .reply import human_chunks, split_text, typing_delay
from .roles import Role
from .runtime import RuntimeFacts
from .safety import SafetyPolicy, build_safety
from .target import classify, combine, score_style
from .tools import REGISTRY as CORE_TOOLS
from .tools import ToolContext

log = logging.getLogger(__name__)

TYPING_REFRESH = 8
RESTART_FIELDS = ("workspace", "gateways", "sandbox", "web_search", "mcp", "database", "paths")
DECISION_HISTORY = 30
DECISION_FALLBACK_HISTORY = 20
DECISION_LINE_CHARS = 500
MAX_TEXT = 10_000
MENTION_HEAD_RE = r"^@([^\s@]+)\s*"


def command_body(text: str, bot_name: str, handles: set[str]) -> str:
    """Buang mention bot di awal pesan: '@Sanka /mood' -> '/mood'."""
    import re

    body = str(text or "").lstrip()
    pattern = re.compile(MENTION_HEAD_RE)
    match = pattern.match(body)
    while match is not None:
        handle = match.group(1).lstrip("+")
        digits = "".join(ch for ch in handle if ch.isdigit()) or handle.lower()
        if handle.casefold() != str(bot_name or "").casefold() and digits not in handles:
            break
        body = body[match.end() :]
        match = pattern.match(body)
    return body


def _stale(answered: dict[str, frozenset[str]], chat_id: str, message: ChatMessage) -> bool:
    return bool(message.id) and message.id in answered.get(chat_id, frozenset())


class Agent:
    """Satu proses, satu hati: semua gateway memanggil `handle` yang sama."""

    def __init__(
        self,
        config: Config,
        db,
        *,
        llm: LLM | None,
        hub=None,
        windows: WorkingMemory,
        history,
        memory: MemoryBackend,
        ledger,
        skills=None,
        recipes=None,
        affect=None,
        style=None,
        ambient=None,
        currents=None,
        vibe=None,
        gateways: tuple[Gateway, ...] = (),
        documents=None,
        approvals: ApprovalStore | None = None,
        safety: SafetyPolicy | None = None,
        runtime: RuntimeFacts | None = None,
    ) -> None:
        self.config = config
        self.soul = config.soul
        self.db = db
        self.llm = llm
        self.hub = hub
        self.windows = windows
        self.history = history
        self.memory = memory
        self.ledger = ledger
        self.skills = skills
        self.recipes = recipes
        self.affect = affect
        self.style = style
        self.ambient = ambient
        self.currents = currents
        self.vibe = vibe
        self.gateways = tuple(gateways)
        self.documents = documents
        self.approvals = approvals or ApprovalStore(db, config.approval)
        self.safety = safety or build_safety(config.approval)
        self.mode = "standby"
        self.lease = None
        self.turn_lock = asyncio.Lock()
        self.tasks: set[asyncio.Task] = set()
        self.answered: dict[str, frozenset[str]] = {}
        self.backlog: dict[str, int] = {}
        self.quiet: dict[str, float] = {}
        handlers = list(CORE_TOOLS.by_name.values())
        for gateway in self.gateways:
            handlers.extend(gateway.tools())
        self.tools = Registry(handlers, "tool")
        handlers = list(CORE_COMMANDS.by_name.values())
        for gateway in self.gateways:
            handlers.extend(gateway.commands())
        self.commands = Registry(handlers, "command")
        self.runtime = runtime or RuntimeFacts(
            db_label=getattr(db, "label", ""), paths=config.paths
        )
        self.runtime.paths = config.paths
        self.runtime.db_label = getattr(db, "label", "")
        self.runtime.gateways = tuple(gateway.name for gateway in self.gateways)
        self.runtime.memory_status = memory_status(memory)
        self.privacy = PrivacyService(self)

    # ------------------------------------------------------------- util

    def spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def _role_set(self, inbound: Inbound) -> tuple[Role, ...]:
        if inbound.role_override:
            return self.config.roles.override(inbound.role_override)
        return self.config.roles.for_address(inbound.message.sender_jid)

    @staticmethod
    def _sender(inbound: Inbound) -> str:
        return inbound.message.sender_id or inbound.message.sender_jid or inbound.chat_id

    async def _target(
        self, inbound: Inbound, gateway: Gateway, messages, message
    ) -> tuple[str, float, str]:
        """Gender target: override manual, lalu gaya mengetik, avatar hanya bila diminta."""
        recent = [
            entry.text
            for entry in messages[-8:]
            if entry.sender_id == message.sender_id and entry.text.strip()
        ]
        style = score_style(recent)
        stored = ("", 0.0, "")
        if self.affect is not None:
            try:
                stored = self.affect.gender(inbound.person_id)
            except Exception:
                log.debug("gender: gagal membaca simpanan", exc_info=True)
        if stored[0] and stored[2] == "manual":
            return stored
        avatar = None
        if style[0] == "unknown" and self.config.character.gender_avatar:
            grab = getattr(gateway, "avatar", None)
            if callable(grab):
                try:
                    shot = await grab(message.sender_jid)
                except Exception:
                    log.debug("gender: avatar gagal diambil", exc_info=True)
                    shot = None
                if shot:
                    avatar = await classify(self.llm, shot[0], shot[1])
        target, confidence, source = combine(
            style, avatar, gate=self.config.character.gender_gate
        )
        if self.affect is not None and (target, confidence, source) != tuple(stored):
            try:
                self.affect.set_gender(inbound.person_id, target, confidence, source)
            except Exception:
                log.debug("gender: gagal menyimpan label", exc_info=True)
        return (target, confidence, source)

    def tool_context(
        self,
        chat_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: Gateway | None,
        *,
        approved: bool = False,
        approval_id: str = "",
        request: str = "",
        person_id: str = "",
        is_dm: bool = False,
    ) -> ToolContext:
        return ToolContext(
            self.config,
            roles,
            tool_patterns(roles),
            chat_id=chat_id,
            sender=sender,
            request=request,
            history=self.history,
            memory=self.memory,
            ledger=self.ledger,
            skills=self.skills,
            recipes=self.recipes,
            affect=self.affect,
            agent=self,
            gateway=gateway,
            approved=approved,
            approval_id=approval_id,
            person_id=person_id,
            is_dm=is_dm,
        )

    def command_context(
        self,
        chat_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: Gateway | None,
        args: str = "",
        inbound: Inbound | None = None,
    ) -> CommandContext:
        specs = tools_for(self.tools, roles) + (self.hub.specs(roles) if self.hub else [])
        return CommandContext(
            agent=self,
            gateway=gateway,
            roles=roles,
            chat_id=chat_id,
            sender=sender,
            args=args,
            commands=tuple(commands_for(self.commands, roles)),
            tools=tuple(specs),
            inbound=inbound,
        )

    def _specs(self, roles: tuple[Role, ...]) -> list[Handler]:
        specs = list(tools_for(self.tools, roles))
        if self.hub is not None:
            specs += self.hub.specs(roles)
        return specs

    def _decision_transcript(self, chat_id: str) -> str:
        entries: list[ChatMessage] = []
        try:
            entries = list(self.history.recent(chat_id, DECISION_HISTORY))
        except Exception:  # arsip rusak tidak boleh menggagalkan keputusan
            log.debug("gagal membaca arsip untuk keputusan chat=%s", chat_id, exc_info=True)
        if not entries:
            entries = self.windows.get(chat_id)[-DECISION_FALLBACK_HISTORY:]
        lines = [
            prompt_entry(entry, self.soul.name, DECISION_LINE_CHARS) for entry in entries
        ]
        return "\n".join(line for line in lines if line)

    # ------------------------------------------------------------ keluar

    def _self_sender_id(self, gateway: Gateway) -> str:
        return next(iter(sorted(gateway.self_handles())), "")

    async def deliver_text(
        self,
        chat_id: str,
        text: str,
        *,
        gateway: Gateway,
        reply: Inbound | None = None,
    ) -> str:
        message_id = await gateway.send_text(chat_id, text, reply=reply)
        self.windows.append(
            chat_id,
            ChatMessage(
                id=message_id,
                sender_name=self.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=text,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        return message_id

    async def deliver_file(
        self,
        chat_id: str,
        path: Path,
        caption: str = "",
        *,
        gateway: Gateway,
        reply: Inbound | None = None,
    ) -> str:
        message_id = await gateway.send_file(chat_id, path, caption, reply=reply)
        note = f"(file terkirim: {path.name}{f' — {caption}' if caption else ''})"
        self.windows.append(
            chat_id,
            ChatMessage(
                id=message_id,
                sender_name=self.soul.name,
                sender_id=self._self_sender_id(gateway),
                when=datetime.now(),
                text=note,
                outgoing=True,
                author=AUTHOR_BOT,
            ),
        )
        return message_id

    async def deliver_reaction(
        self, chat_id: str, message_id: str, *, gateway: Gateway
    ) -> str | None:
        label = await gateway.send_reaction(chat_id, message_id)
        if label:
            self.windows.append(
                chat_id,
                ChatMessage(
                    id=f"react-{time.time_ns()}",
                    sender_name=self.soul.name,
                    sender_id=self._self_sender_id(gateway),
                    when=datetime.now(),
                    text=label,
                    outgoing=True,
                    author=AUTHOR_BOT,
                ),
            )
        return label

    async def send_text(self, chat_id: str, text: str, *, gateway: Gateway | None) -> str:
        """Tool-facing: render dialek gateway, pecah, kirim, kembalikan ok:/error:."""
        if gateway is None:
            return "error: gateway tidak tersedia"
        body = clean(str(text or "")).strip()
        if not body:
            return "error: teks kosong"
        chunks = split_text(gateway.render(body), limit=MAX_TEXT)
        if not chunks:
            return "error: teks kosong"
        try:
            for chunk in chunks:
                await self.deliver_text(chat_id, chunk, gateway=gateway)
        except GatewayError as exc:
            return f"error: gagal mengirim teks: {exc}"
        name = gateway.chat_name(chat_id)
        suffix = f" ({len(chunks)} bagian)" if len(chunks) > 1 else ""
        return f"ok: teks terkirim ke {name or chat_id}{suffix}"

    async def send_file(
        self, chat_id: str, path: Path, caption: str = "", *, gateway: Gateway | None
    ) -> str:
        if gateway is None:
            return "error: gateway tidak tersedia"
        try:
            await self.deliver_file(chat_id, Path(path), caption, gateway=gateway)
        except GatewayError as exc:
            return f"error: gagal mengirim file: {exc}"
        except OSError as exc:
            return f"error: {type(exc).__name__}: {exc}"
        return f"ok: file terkirim: {Path(path).name}"

    # ------------------------------------------------------------- turn

    async def handle(self, inbound: Inbound, gateway: Gateway) -> None:
        message = inbound.message
        chat_id = inbound.chat_id
        self.windows.append(chat_id, message)
        if self.ambient is not None:
            self.ambient.note(chat_id, message)
        if message.outgoing or inbound.is_self:
            return
        age = time.time() - message.when.timestamp()
        if gateway.backlog_seconds > 0 and age > gateway.backlog_seconds:
            self.backlog[chat_id] = self.backlog.get(chat_id, 0) + 1
            log.info("pesan lama chat=%s umur=%.0fs: arsip saja", chat_id, age)
            return

        text = message.text
        crisis = guard.is_crisis(text)
        roles = self._role_set(inbound)
        handle_set = gateway.self_handles()
        body = command_body(text, self.soul.name, handle_set)
        is_command = body.startswith("/")
        if not is_command and await self.privacy.handle_pending(
            inbound, gateway, self._sender(inbound)
        ):
            return
        if not is_command and not crisis and self.quiet.get(chat_id, 0.0) > time.time():
            log.info("quiet chat=%s: pesan dicatat tanpa dibalas", chat_id)
            return
        if not is_command and not crisis and self.llm is None:
            return
        head_word, _, rest = body[1:].partition(" ") if is_command else ("", "", "")
        name = head_word.lower()
        direct = inbound.is_dm or inbound.is_mention or inbound.is_reply

        queue = contextlib.nullcontext() if is_command else self.turn_lock
        async with queue:
            if not is_command and _stale(self.answered, chat_id, message):
                log.info("pesan basi chat=%s: sudah terwakili burst sebelumnya", chat_id)
                return
            missed = self.backlog.pop(chat_id, 0) if not is_command else 0
            away = away_block(missed)
            vibe = ""
            if self.vibe is not None and not is_command and not crisis and text.strip():
                try:
                    vibe = await self.vibe.observe(chat_id, text)
                except Exception:
                    log.exception("gagal memperbarui vibe chat=%s", chat_id)
            emotion_block = ""
            turn = None
            if not is_command and self.affect is not None:
                try:
                    turn = self.affect.turn_start(chat_id, message.id)
                except Exception:
                    log.exception("gagal membuka turn emosi chat=%s", chat_id)
                try:
                    emotion_block = self.affect.prompt(chat_id, inbound.person_id)
                except Exception:
                    log.exception("gagal membaca state emosi chat=%s", chat_id)

            called = inbound.is_mention or inbound.is_reply
            unprompted = not inbound.is_dm and not is_command and not crisis and not called
            decision: Decision | None
            safety = ""
            if crisis:
                decision = Decision(
                    addressed="me",
                    act="reply",
                    mode="daily",
                    tone="neutral",
                    bubbles=1,
                    appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                    relationship=RelationshipDelta(),
                    reason="crisis",
                )
                safety = CRISIS_SAFETY
            elif is_command:
                decision = None
            elif unprompted:
                decision = Decision(
                    addressed="group",
                    act="ignore",
                    mode="daily",
                    tone="neutral",
                    bubbles=1,
                    appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                    relationship=RelationshipDelta(),
                    reason="group without a call",
                )
            else:
                decision = await decide(
                    self.llm,
                    chat_id=chat_id,
                    chat_name=gateway.chat_name(chat_id),
                    current=prompt_entry(message, self.soul.name, DECISION_LINE_CHARS),
                    transcript=self._decision_transcript(chat_id),
                    emotion=turn,
                    relationship=emotion_block,
                    is_dm=inbound.is_dm,
                    is_mention=inbound.is_mention,
                    is_reply=inbound.is_reply,
                    away=away,
                    vibe=vibe,
                    soul=self.soul.persona,
                    language=self.soul.language,
                )
                if decision is None:
                    log.warning("keputusan kosong, lanjut tanpa nada: %s", chat_id)
                    decision = Decision(
                        addressed="me" if direct else "unknown",
                        act="reply" if direct else "ignore",
                        mode="daily",
                        tone="curt",
                        bubbles=1,
                        appraisal=Appraisal("neutral", 0.0, 0.0, 0.0),
                        relationship=RelationshipDelta(),
                        reason="model did not answer",
                    )
                elif direct:
                    decision = ensure_reply(decision)
                if turn is not None:
                    annoyed = 0.0
                    if self.affect is not None:
                        try:
                            annoyed = next(
                                (
                                    float(row["annoyance"])
                                    for row in self.affect.known_people(chat_id, 20)
                                    if row["person_id"] == inbound.person_id
                                ),
                                0.0,
                            )
                        except Exception:
                            log.debug("gagal membaca annoyance chat=%s", chat_id, exc_info=True)
                    grounded = ground_tone(decision, turn, annoyed)
                    if grounded.tone != decision.tone:
                        log.info(
                            "nada %s->%s chat=%s: perasaan tidak cocok",
                            decision.tone,
                            grounded.tone,
                            chat_id,
                        )
                    decision = grounded

            if not is_command and self.affect is not None:
                try:
                    self.affect.turn_end(
                        chat_id,
                        inbound.person_id,
                        message.sender_name,
                        message.text,
                        roles[0].name,
                        message.sender_jid,
                        None if crisis else decision,
                        message.id,
                    )
                except Exception:
                    log.exception("gagal menutup turn emosi chat=%s", chat_id)

            if not is_command:
                if decision.act == "ignore":
                    log.info(
                        "diam chat=%s addressed=%s reason=%s",
                        chat_id,
                        decision.addressed,
                        decision.reason,
                    )
                    return
                if decision.act == "react":
                    await self.deliver_reaction(chat_id, message.id, gateway=gateway)
                    return

            await gateway.typing(chat_id, True)
            typing = asyncio.create_task(self._keep_typing(chat_id, gateway))
            try:
                log.info(
                    "pesan chat=%s sender=%s roles=%s act=%s mode=%s tone=%s addressed=%s",
                    chat_id,
                    message.sender_id,
                    ",".join(role.name for role in roles),
                    getattr(decision, "act", "command"),
                    getattr(decision, "mode", "-"),
                    getattr(decision, "tone", "-"),
                    getattr(decision, "addressed", "-"),
                )
                covered = frozenset({message.id})
                reply: str | None = None
                can_memory = False
                memory_block = style_block = topic_block = person_style = ""
                memory_ctx = MemoryContext()
                if is_command:
                    reply = await self._command(
                        name,
                        rest.strip(),
                        chat_id,
                        self._sender(inbound),
                        roles,
                        gateway,
                        inbound,
                    )
                else:
                    specs = self._specs(roles)
                    can_memory = any(spec.name == "memory" for spec in specs)
                    cross_send = any(spec.name == "send_to" for spec in specs)
                    mood = None
                    if turn is not None:
                        mood = ((float(turn["pleasure"]) + 1.0) / 2.0, float(turn["arousal"]))
                    try:
                        memory_ctx = await self.memory.context(
                            person_id=inbound.person_id,
                            chat_id=chat_id,
                            query=message.text,
                            mood=mood,
                            max_items=self.config.memory.max_items,
                            max_chars=self.config.memory.max_chars,
                        )
                        memory_block = memory_ctx.text
                    except Exception:
                        log.exception("gagal membaca memori chat=%s", chat_id)
                    actions_block = ""
                    if self.ledger is not None:
                        try:
                            actions_block = self.ledger.actions_context()
                        except Exception:
                            log.exception("gagal membaca ledger aksi chat=%s", chat_id)
                    skill_block = ""
                    if self.skills is not None and any(spec.name == "skill" for spec in specs):
                        try:
                            skill_block = self.skills.index()
                        except Exception:
                            log.exception("gagal membaca indeks skill")
                    if self.affect is not None:
                        try:
                            topic_block = self.affect.topic_block(chat_id)
                        except Exception:
                            log.exception("gagal membaca topik chat=%s", chat_id)
                    if self.style is not None:
                        try:
                            style_block = self.style.profile(chat_id)
                            person_style = self.style.person_profile(inbound.person_id)
                        except Exception:
                            log.debug("gaya: gagal membaca profil chat=%s", chat_id, exc_info=True)
                    window = self.windows.get(chat_id)
                    for entry in window:
                        with contextlib.suppress(Exception):
                            await gateway.prepare_media(chat_id, entry)
                    messages = self.windows.through(chat_id, message)
                    if any(entry is message for entry in window):
                        messages = window
                    covered = frozenset(entry.id for entry in messages if entry.id)
                    target, confidence, source = await self._target(
                        inbound, gateway, messages, message
                    )
                    limits = effective_limits(self.config.character, target, confidence)
                    register = room_register(
                        [entry.text for entry in messages[-6:] if entry.text.strip()]
                    )
                    patterns = select_patterns(
                        self.config.character.patterns,
                        register=register,
                        target=target,
                        limits=limits,
                        messages=[entry.text for entry in messages[-8:]],
                    )
                    facts = select_facts(
                        self.config.character.facts,
                        messages=[entry.text for entry in messages[-8:]],
                    )
                    character = character_block(
                        limits,
                        target,
                        confidence,
                        gate=self.config.character.gender_gate,
                        jabs=self.config.character.roast_jabs,
                        patterns=patterns,
                        facts=facts,
                    )
                    currents_block = ""
                    if self.currents is not None:
                        try:
                            currents_block = self.currents.block(time.time(), 6)
                        except Exception:
                            log.debug("currents: gagal membaca konteks", exc_info=True)
                    try:
                        content = await build_user_content(
                            self.config,
                            chat_id,
                            messages,
                            message,
                            chat_name=gateway.chat_name(chat_id),
                        )
                    except Exception:
                        log.exception("gagal merakit payload chat=%s", chat_id)
                        content = fallback_content(self.config, message)
                    tool_ctx = self.tool_context(
                        chat_id,
                        self._sender(inbound),
                        roles,
                        gateway,
                        request=message.text,
                        person_id=inbound.person_id,
                        is_dm=inbound.is_dm,
                    )
                    bonuses: dict[str, float] = {}
                    if self.affect is not None:
                        try:
                            person_row = next(
                                (
                                    row
                                    for row in self.affect.known_people(chat_id, 20)
                                    if row["person_id"] == inbound.person_id
                                ),
                                None,
                            )
                            bonuses = {
                                "topic": 0.3 if self.affect.pending(chat_id) > 0 else 0.0,
                                "person_style": (
                                    0.25
                                    if person_row and int(person_row["interactions"]) >= 15
                                    else 0.0
                                ),
                                "style": (
                                    0.15
                                    if person_row and float(person_row["familiarity"]) >= 0.3
                                    else 0.0
                                ),
                                "skills": 0.2 if decision.mode == "professional" else 0.0,
                            }
                        except Exception:
                            log.debug("gagal menghitung bonus salience chat=%s", chat_id, exc_info=True)
                            bonuses = {}
                    bonuses["memory"] = (
                        0.35
                        if memory_block
                        and keyword_set(message.text) & keyword_set(memory_block)
                        else 0.0
                    )
                    answer = await self.llm.run(
                        system_prompt(
                            self.config,
                            roles,
                            runtime=self.runtime,
                            gateway_hints=gateway.prompt_hints(),
                            memory=memory_block,
                            actions=actions_block,
                            skills=skill_block,
                            cross_send=cross_send,
                            topic=topic_block,
                            away=away,
                            vibe=vibe,
                            emotion=emotion_block,
                            style=style_block,
                            person_style=person_style,
                            safety=safety,
                            character=character,
                            research=research_block(),
                            currents=currents_block,
                            sender=message.sender_name,
                            is_dm=inbound.is_dm,
                            mode=decision.mode,
                            tone=decision.tone,
                            decision_reason=decision.reason,
                            query=message.text,
                            bonuses=bonuses,
                        ),
                        content,
                        specs,
                        tool_ctx,
                        session=f"sanka-{chat_id}",
                    )
                    reply = (
                        answer
                        if answer.strip() or not tool_ctx.sent
                        else t("agent.sent_ack", self.soul.language)
                    )
                    daily = decision is None or decision.mode != "professional"
                    if (
                        daily
                        and not is_command
                        and not crisis
                        and reply
                        and not tool_ctx.sent
                    ):
                        recent = "\n".join(
                            f"{entry.sender_name}: {entry.text}"
                            for entry in messages[-6:]
                            if entry.text.strip()
                        )
                        before = reply
                        reply = await polish(
                            self.llm,
                            self.config,
                            reply,
                            latest=message.text,
                            recent=recent,
                            is_dm=inbound.is_dm,
                            tone=decision.tone if decision is not None else "neutral",
                            session=f"sanka-character-{chat_id}",
                        )
                        log.info(
                            "character chat=%s status=%s",
                            chat_id,
                            "rewrite" if reply != before else "pass",
                        )
                        if not reply.strip():
                            reply = before
                    if reply and not is_command and not crisis:
                        before = reply
                        reply = await regulate(
                            self.llm,
                            self.config,
                            reply,
                            target=target,
                            confidence=confidence,
                            latest=message.text,
                            session=f"sanka-character-{chat_id}",
                            is_professional=bool(
                                decision is not None and decision.mode == "professional"
                            ),
                        )
                        log.info(
                            "level chat=%s target=%s/%s status=%s",
                            chat_id,
                            target,
                            f"{confidence:.2f}",
                            "regulated" if reply != before else "pass",
                        )
                    review = await self.privacy.review(
                        inbound,
                        reply,
                        [*memory_ctx.hits, *tool_ctx.memory_hits],
                        gateway,
                    )
                    if review.handled:
                        return
                    reply = review.reply
                    if reply and tool_ctx.cross_reads and guard.cross_chat_leak(
                        reply, tool_ctx.cross_reads
                    ):
                        log.warning("cross-chat verbatim reply blocked chat=%s", chat_id)
                        reply = t("agent.cross_chat_blocked", self.soul.language)

                professional = bool(decision is not None and decision.mode == "professional")
                humanize = self.config.humanize
                if is_command or professional:
                    chunks = split_text(gateway.render(reply or "")) or [
                        t("agent.empty_reply", self.soul.language)
                    ]
                    pace = False
                    quote_all = True
                else:
                    planned = max(1, int(humanize.max_bubbles))
                    chunks = [
                        gateway.render(chunk)
                        for chunk in human_chunks(reply or "", max_parts=planned)
                    ] or [t("agent.empty_reply", self.soul.language)]
                    pace = bool(humanize.enabled)
                    quote_all = not pace
                delivered = True
                delivered_ids: list[str] = []
                for index, chunk in enumerate(chunks):
                    if pace:
                        await gateway.typing(chat_id, True)
                        await asyncio.sleep(typing_delay(chunk, humanize))
                    try:
                        delivered_ids.append(
                            await self.deliver_text(
                                chat_id,
                                chunk,
                                gateway=gateway,
                                reply=inbound if (quote_all or index == 0) else None,
                            )
                        )
                    except GatewayError:
                        log.exception("gagal kirim balasan ke %s", chat_id)
                        delivered = False
                        break
                    if pace and index < len(chunks) - 1:
                        await asyncio.sleep(
                            max(0.0, float(humanize.between_delay)) + random.uniform(0, 0.4)
                        )
                if delivered and covered:
                    self.answered[chat_id] = covered
                self.runtime.pending_approvals = self.approvals.count_pending()
                self.approvals.expire()
                capture = (
                    reply
                    and not is_command
                    and not crisis
                    and can_memory
                    and self.config.memory.auto_capture
                    and len(str(message.text or "").strip())
                    >= int(self.config.memory.capture_min_chars)
                    and not guard.is_injection(message.text)
                )
                if capture:
                    self.spawn(
                        self.privacy.capture_turn(
                            inbound, reply or "", gateway, tuple(delivered_ids)
                        )
                    )
            finally:
                typing.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await typing
                await gateway.typing(chat_id, False)

    async def _keep_typing(self, chat_id: str, gateway: Gateway) -> None:
        while True:
            await asyncio.sleep(TYPING_REFRESH)
            await gateway.typing(chat_id, True)

    async def _command(
        self,
        name: str,
        args: str,
        chat_id: str,
        sender: str,
        roles: tuple[Role, ...],
        gateway: Gateway,
        inbound: Inbound,
    ) -> str:
        if not name:
            return t("agent.help_hint", self.soul.language)
        handler = self.commands.get(name)
        recipe = None
        if handler is None and self.recipes is not None:
            with contextlib.suppress(Exception):
                recipe = self.recipes.get(name)
        if handler is None and recipe is None:
            return t("agent.unknown_command", self.soul.language, name=name)
        names = {role.name for role in roles}
        denied = t(
            "agent.command_denied",
            self.soul.language,
            name=name,
            roles=", ".join(sorted(names)),
        )
        if recipe is not None and not (names & set(recipe.roles)):
            return denied
        if handler is not None and not command_allowed(roles, handler.name):
            return denied
        if recipe is not None:
            from .stores.recipes import run_recipe

            ctx = self.tool_context(chat_id, sender, roles, gateway, request=args)
            if recipe.kind == "recipe":
                return await run_recipe(recipe, args, ctx)
            if self.llm is None:
                return t("agent.no_llm", self.soul.language)
            specs = self._specs(roles)
            return await self.llm.run(
                system_prompt(
                    self.config,
                    roles,
                    runtime=self.runtime,
                    gateway_hints=gateway.prompt_hints(),
                    mode="professional",
                ),
                f"{recipe.prompt}\n\nArguments: {args}".strip(),
                specs,
                ctx,
                session=f"sanka-recipe-{chat_id}-{name}",
            )
        ctx = self.command_context(chat_id, sender, roles, gateway, args, inbound)
        return await handler.fn(ctx)

    # ------------------------------------------------------------ hidup

    def request_freeze(self) -> None:
        if self.lease is not None:
            self.lease.request_freeze(self.lease.instance_id)

    def _restart_tokens(self, config: Config) -> dict[str, object]:
        memory = config.memory
        return {
            "workspace": config.workspace,
            "gateways": config.gateways,
            "sandbox": config.sandbox,
            "web_search": config.web_search,
            "mcp": config.mcp,
            "database": config.database,
            "paths": config.paths,
            "llm.api_key_env": config.llm.api_key_env,
            "memory.backend": memory.backend,
            "memory.collection": memory.collection,
            "memory.embedder": memory.embedder,
            "memory.extraction_model": memory.extraction_model,
            "affect.enabled": config.affect.enabled,
            "affect.vibe.enabled": config.affect.vibe.enabled,
            "affect.vibe.model": config.affect.vibe.model,
            "ambient.enabled": config.ambient.enabled,
            "style.enabled": config.style.enabled,
        }

    async def reload(self) -> str:
        """Muat ulang config/soul/role; tolak perubahan yang butuh restart."""
        from .config import ConfigError, load_config

        reload_catalogs()
        loaded = load_config(
            self.config.paths.config, documents=self.documents, require_key=False
        )
        before = self._restart_tokens(self.config)
        after = self._restart_tokens(loaded)
        changed = [key for key in before if before[key] != after[key]]
        if changed:
            raise ConfigError("restart required: " + ", ".join(changed))
        old_llm = self.config.llm
        self.config = loaded
        self.soul = loaded.soul
        if self.llm is not None and loaded.llm != old_llm:
            self.llm = LLM(loaded)
        if self.skills is not None:
            self.skills.max_index_chars = max(200, int(loaded.skills.max_index_chars))
        if self.style is not None:
            self.style.config = loaded.style
        if self.affect is not None:
            self.affect.config = loaded.affect
        if self.vibe is not None:
            self.vibe.config = loaded.affect.vibe
        configure = getattr(self.memory, "configure_limits", None)
        if callable(configure):
            configure(loaded.memory)
        self.approvals.configure(loaded.approval)
        self.safety = build_safety(loaded.approval)
        self.privacy.configure(loaded.memory.privacy)
        if self.ambient is not None:
            self.ambient.config = loaded
            self.ambient.settings = loaded.ambient
            self.ambient.llm = self.llm
        self.runtime.paths = loaded.paths
        self.runtime.memory_status = memory_status(self.memory)
        return "config/soul/roles reloaded"

    async def apply_settings_payload(self, payload: dict) -> str:
        from .tools.settings import apply_payload

        return await apply_payload(self, payload)

    async def close(self) -> None:
        for task in list(self.tasks):
            task.cancel()
        if self.tasks:
            await asyncio.gather(*self.tasks, return_exceptions=True)
        for gateway in self.gateways:
            with contextlib.suppress(Exception):
                await gateway.stop()
        if self.ambient is not None:
            with contextlib.suppress(Exception):
                await self.ambient.stop()
        if self.vibe is not None:
            with contextlib.suppress(Exception):
                await self.vibe.close()
        with contextlib.suppress(Exception):
            await self.memory.close()
        if self.hub is not None:
            with contextlib.suppress(Exception):
                await self.hub.stop()
        with contextlib.suppress(Exception):
            self.db.dispose()
