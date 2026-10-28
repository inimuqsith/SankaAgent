"""Otonomi terbatas: ringkas topik dan riset saat sepi.

Semua akses di luar efek emosi dibatasi: tool hanya `web_search`, memori hanya chat yang
sama + global, dan tidak pernah mengirim pesan ke chat mana pun.
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import date, datetime

from . import guard
from .config import Config, RoleConfig
from .tools import ToolContext, execute
from .tools.web_search import TOOL as WEB_SEARCH

log = logging.getLogger(__name__)

TRANSCRIPT_CHARS = 300
AMBIENT_ROLE = ("web_search",)


class Ambient:
    """Loop latar satu task; semua kegagalan ditelan agar bot tetap membalas."""

    def __init__(
        self,
        config: Config,
        llm=None,
        affect=None,
        style=None,
        memory=None,
        history=None,
        sessions=None,
    ):
        self.config = config
        self.settings = config.ambient
        self.llm = llm
        self.affect = affect
        self.style = style
        self.memory = memory
        self.history = history
        self.sessions = sessions
        self.known_chats: set[str] = set()
        self.busy: set[str] = set()
        self.tasks: set[asyncio.Task] = set()
        self._task: asyncio.Task | None = None

    # ------------------------------------------------------------------ lifecycle

    def start(self) -> None:
        if self._task is None:
            self.prime()
            self._task = asyncio.create_task(self._loop())

    def prime(self) -> None:
        """Chat lama ikut berdetak setelah restart, bukan hanya chat yang baru lewat."""
        if self.history is None:
            return
        try:
            chats = self.history.recent_chats(50)
        except Exception:
            log.debug("ambient: gagal membaca chat terakhir", exc_info=True)
            return
        self.known_chats.update(str(chat_id) for chat_id in chats if str(chat_id))

    async def stop(self) -> None:
        pending = [task for task in (self._task, *self.tasks) if task is not None]
        for task in pending:
            task.cancel()
        if pending:
            await asyncio.gather(*pending, return_exceptions=True)
        self._task = None
        self.tasks.clear()

    def _spawn(self, coro) -> None:
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def note(self, chat_id: str, message) -> None:
        """Pesan lewat: cukup catat chat aktif. Appraisal/pending sudah diurus AffectStore."""
        if not chat_id:
            return
        self.known_chats.add(chat_id)

    async def _loop(self) -> None:
        while True:
            await asyncio.sleep(max(10, int(self.settings.tick_seconds)))
            try:
                await self._tick()
            except asyncio.CancelledError:
                raise
            except Exception:  # otonomi tidak boleh mematikan bot
                log.exception("ambient tick gagal")

    # ------------------------------------------------------------------ tick

    async def _tick(self, now: float | None = None) -> None:
        if self.affect is None or self.history is None or self.llm is None:
            return
        moment = time.time() if now is None else float(now)
        settings = self.settings
        chats = sorted(self.known_chats)
        for chat_id in chats:
            try:  # heartbeat: mood meluruh dan drive tumbuh walau tidak ada pesan
                self.affect.pulse(chat_id)
            except Exception:
                log.debug("heartbeat pulse gagal chat=%s", chat_id, exc_info=True)
        for chat_id in chats:
            if chat_id in self.busy:
                continue
            try:
                topic = self.affect.topic(chat_id)
                pending = self.affect.pending(chat_id)
            except Exception:
                log.debug("ambient: topik gagal chat=%s", chat_id, exc_info=True)
                continue
            if pending >= int(settings.digest_every) and (
                moment - float(topic["last_digest"]) >= float(settings.digest_cooldown)
            ):
                self.busy.add(chat_id)
                self._spawn(self._digest(chat_id))
                log.info("ambient digest mulai chat=%s pending=%d", chat_id, pending)
        if settings.proactive:
            for chat_id in chats:
                if await self._research_candidate(chat_id, moment):
                    await self._research(chat_id)
                    break

    async def _research_candidate(self, chat_id: str, moment: float) -> bool:
        settings = self.settings
        try:
            topic = self.affect.topic(chat_id)
        except Exception:
            return False
        if moment - float(topic["last_message"]) < int(settings.idle_minutes) * 60:
            return False
        if not [item for item in topic["unresolved"] if str(item).strip()]:
            return False
        if moment - float(topic["last_research"]) < int(settings.research_cooldown):
            return False
        return int(topic["research_count"]) < int(settings.research_per_day)

    # ------------------------------------------------------------------ aksi

    async def _digest(self, chat_id: str) -> None:
        try:
            if self.affect is None or self.history is None or self.llm is None:
                return
            topic = self.affect.topic(chat_id)
            rows = [
                row
                for row in self._recent(chat_id, int(self.settings.history_window))
                if not getattr(row, "outgoing", False)
            ]
            if not rows:
                self.affect.mark_digest(chat_id)
                return
            transcript = "\n".join(self._line(row) for row in rows)
            people = ", ".join(
                str(item["name"]) for item in self.affect.known_people(chat_id, 8) if item["name"]
            )
            name = ""
            if self.sessions is not None:
                try:
                    name = self.sessions.chat_name(chat_id)
                except Exception:
                    name = ""
            data = await self.llm.digest(name, transcript, str(topic["summary"]), people)
            if not isinstance(data, dict):
                return
            summary = str(data.get("topic") or "")
            keywords = [str(item) for item in data.get("keywords") or []]
            unresolved = [str(item) for item in data.get("unresolved") or []]
            if summary or keywords or unresolved:
                self.affect.set_topic(chat_id, summary, keywords, unresolved)
            self.affect.mark_digest(chat_id)
            for fact in data.get("facts") or []:
                if not isinstance(fact, dict):
                    continue
                text = str(fact.get("text") or "").strip()
                if not text or guard.SENSITIVE.search(text) or guard.INJECTION.search(text):
                    continue
                person = str(fact.get("person") or "").strip()
                person_id = self.affect.person_by_name(chat_id, person) if person else None
                if person_id:
                    self.affect.note_person(person_id, text)
                    continue
                if self.memory is None:
                    continue
                try:
                    self.memory.add(
                        chat_id,
                        str(fact.get("kind") or "note"),
                        text,
                        source=f"ambient:{chat_id}",
                    )
                except ValueError:
                    continue
            log.info("ambient digest selesai chat=%s", chat_id)
        except asyncio.CancelledError:
            raise
        except Exception:  # digest gagal tidak boleh menghentikan loop
            log.exception("ambient digest gagal chat=%s", chat_id)
        finally:
            self.busy.discard(chat_id)

    async def _research(self, chat_id: str) -> None:
        if self.affect is None or self.llm is None:
            return
        topic = self.affect.topic(chat_id)
        pending = [str(item).strip() for item in topic["unresolved"] if str(item).strip()]
        summary = str(topic["summary"]).strip()
        query = pending[0] if pending else (f"topik: {summary}" if summary else "")
        if not query:
            return
        try:
            results = await execute(
                {"web_search": WEB_SEARCH},
                "web_search",
                {"query": query, "max_results": int(self.settings.research_results)},
                self._tool_ctx(chat_id),
            )
        except Exception:  # tool apa pun tidak boleh mematikan otonomi
            log.exception("ambient riset gagal chat=%s", chat_id)
            return
        if not results or results.startswith("error:"):
            log.info("ambient riset kosong chat=%s: %s", chat_id, results[:120])
            return
        text = await self.llm.research_summary(query, results)
        if not text:
            return
        if self.memory is not None:
            try:
                self.memory.add(
                    chat_id,
                    "note",
                    f"[riset {date.today().isoformat()}] {text}",
                    source=f"ambient:{chat_id}",
                )
            except ValueError:
                return
        self.affect.mark_research(chat_id)
        log.info("ambient riset selesai chat=%s query=%s", chat_id, query[:80])

    # ------------------------------------------------------------------ util

    def _recent(self, chat_id: str, limit: int) -> list:
        if self.history is None:
            return []
        try:
            rows = self.history.recent(chat_id, limit)
        except Exception:
            log.debug("ambient: arsip gagal chat=%s", chat_id, exc_info=True)
            return []
        return [row for row in rows if str(getattr(row, "text", "") or "").strip()]

    @staticmethod
    def _line(row) -> str:
        body = " ".join(str(getattr(row, "text", "") or "").split())
        stamp = ""
        when = getattr(row, "when", None)
        if isinstance(when, datetime):
            stamp = when.strftime("%Y-%m-%d %H:%M")
        who = getattr(row, "sender_name", "") or getattr(row, "sender_id", "") or "-"
        return f"[{stamp or '-'}] {who}: {body[:TRANSCRIPT_CHARS]}"

    def _tool_ctx(self, chat_id: str) -> ToolContext:
        role = RoleConfig(name="ambient", tools=AMBIENT_ROLE)
        return ToolContext(self.config, role, AMBIENT_ROLE, chat_id=chat_id, memory=self.memory)
