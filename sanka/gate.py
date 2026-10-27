"""Gate balasan: Sanka sendiri yang memutuskan reply/react/ignore, dengan pagar di harness.

Keputusan utama datang dari `LLM.decide` (sadar mood dan hubungan). Skor deterministik di
bawah adalah prior/fallback, dipakai saat LLM mati, saat `llm_decide` dimatikan, dan untuk
memutuskan chime otonom di `ambient.py`. Rate limit selalu menang agar Sanka tidak spam.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

from . import guard

log = logging.getLogger(__name__)

DECIDE_PROMPT = """Kamu Sanka, laki-laki dengan ekspresi emosional feminin, sedang di grup WhatsApp.
Baca percakapan dan pesan terakhir. Tentukan tindakanmu sendiri.
Balas HANYA JSON: {"act":"reply|react|ignore","why":"<=80 char"}
- reply: kamu mau menanggapi.
- react: cukup reaksi singkat/stiker; tidak perlu jawaban.
- ignore: kamu pilih diam; percakapan bukan untukmu, kamu sedang males/kesal, atau tidak perlu.
Pertimbangkan mood, hubungan dengan pengirim, apakah pesan ditujukan kepadamu, dan apakah
kehadiranmu menambah nilai. Jangan menjelaskan proses, jangan menyebut AI."""

JUDGE_PROMPT = """Kamu penilai percakapan grup WhatsApp. Bot bernama "Sanka" baru saja menerima pesan terakhir.
Tugasmu: apakah Sanka perlu membalas?
Balas HANYA satu kata: reply, ignore, atau react.
Aturan:
- reply: pesan ditujukan ke Sanka, bertanya, minta bantuan, mengoreksi, atau melanjutkan obrolan dengannya.
- react: cukup reaksi singkat/stiker; tidak perlu jawaban.
- ignore: percakapan antar manusia, ledekan yang tidak butuh jawaban, spam, atau Sanka baru saja menjawab.
- Pesan yang membalas Sanka tetapi hanya komentar ke orang lain → ignore."""

ACTS = frozenset({"reply", "react", "ignore"})
SECOND_PERSON = re.compile(r"\b(kamu|lu|lo|kau|km|kmu|sanka|san)\b", re.IGNORECASE)
REQUEST = re.compile(r"\b(tolong|coba|bantu|carikan|jelaskan|rekap|kirim|buat)\b", re.IGNORECASE)
HISTORY_LINES = 6
HISTORY_CHARS = 300
PROMPT_CHARS = 700
RATE_WINDOW = 600.0
DECIDE_TIMEOUT = 8.0


@dataclass(frozen=True)
class Decision:
    act: str
    reason: str
    score: float


def contentless(text: str, has_media: bool) -> bool:
    """Pesan hanya stiker/emoji/lampiran tanpa kata: tidak layak dibalas panjang."""
    body = str(text or "").strip()
    if not body:
        return has_media
    return not any(ch.isalnum() for ch in body)


def _words(text: str) -> set[str]:
    return {word.casefold() for word in re.findall(r"\w+", str(text or ""), re.UNICODE)}


class ReplyGate:
    def __init__(
        self,
        config,
        llm=None,
        affect=None,
        sessions=None,
        bot_name: str = "sanka",
        stickers: tuple[str, ...] = (),
    ):
        self.config = config
        self.llm = llm
        self.affect = affect
        self.sessions = sessions
        self.bot_name = bot_name
        self.stickers = tuple(stickers)
        self._sent: dict[str, list[float]] = {}
        self._person_sent: dict[tuple[str, str], list[float]] = {}
        self._ignores: dict[tuple[str, str], int] = {}
        self._reviews: list[float] = []

    # ------------------------------------------------------------------ skor

    def _recent(self, chat_id: str) -> list:
        if self.sessions is None:
            return []
        try:
            return self.sessions.get(chat_id)
        except Exception:  # arsip rusak tidak boleh memblokir keputusan
            log.debug("gagal membaca sesi chat=%s", chat_id, exc_info=True)
            return []

    def _bot_stamps(self, chat_id: str) -> list[float]:
        stamps: list[float] = []
        for entry in self._recent(chat_id):
            if getattr(entry, "outgoing", False):
                try:
                    stamps.append(entry.when.timestamp())
                except (AttributeError, OSError, ValueError):
                    continue
        return stamps

    def score(
        self,
        *,
        chat_id: str,
        text: str,
        is_dm: bool,
        mentioned: bool,
        replied_to_bot: bool,
        sender_id: str,
        has_media: bool,
        quote_sender_is_bot: bool,
        has_quote: bool = False,
        now: float | None = None,
    ) -> tuple[float, str]:
        """Skor deterministik 0..1 (prior/fallback) beserta alasan yang bisa dibaca log."""
        moment = time.time() if now is None else float(now)
        body = str(text or "")
        points = 0.0
        tags: list[str] = []
        if replied_to_bot:
            points += 0.25
            tags.append("replied")
        if quote_sender_is_bot:
            points += 0.10
            tags.append("quote_bot")
        if "?" in body:
            points += 0.20
            tags.append("question")
        if SECOND_PERSON.search(body):
            points += 0.10
            tags.append("second_person")
        if REQUEST.search(body):
            points += 0.10
            tags.append("request")
        if self.affect is not None:
            try:
                keywords = {str(item).casefold() for item in self.affect.topic(chat_id)["keywords"]}
            except Exception:
                keywords = set()
            if len(_words(body) & keywords) >= 2:
                points += 0.10
                tags.append("topic")
        stamps = self._bot_stamps(chat_id)
        if stamps and moment - max(stamps) < 180:
            points += 0.10
            tags.append("recent_reply")
        if self.affect is not None:
            try:
                modes = self.affect.modes(chat_id, sender_id)
            except Exception:
                modes = {}
            if float(modes.get("valence", 0.0)) > 0.3:
                points += 0.05
                tags.append("happy")
            if modes.get("playful"):
                points += 0.05
                tags.append("playful")
            if modes.get("crush"):
                points += 0.10
                tags.append("crush")
            try:
                if self.affect.appeasing(body):
                    points += 0.35
                    tags.append("appeasing")
            except Exception:
                pass
        if has_quote and not quote_sender_is_bot:
            points -= 0.25
            tags.append("to_other")
        recent = self._recent(chat_id)[-3:]
        if recent and not mentioned and all(not getattr(item, "outgoing", False) for item in recent):
            points -= 0.20
            tags.append("human_flow")
        if contentless(body, has_media):
            points -= 0.20
            tags.append("contentless")
        if len(body.strip()) < 4:
            points -= 0.15
            tags.append("short")
        window = [stamp for stamp in stamps if moment - stamp < RATE_WINDOW]
        if len(window) >= 3:
            points -= 0.10
            tags.append("chatty")
        return max(0.0, min(1.0, points)), ",".join(tags) or "casual"

    # ------------------------------------------------------------------ keputusan

    def _over_limit(self, chat_id: str, sender_id: str, moment: float) -> str:
        stamps = [stamp for stamp in self._sent.get(chat_id, []) if moment - stamp < RATE_WINDOW]
        self._sent[chat_id] = stamps
        person = [
            stamp
            for stamp in self._person_sent.get((chat_id, sender_id), [])
            if moment - stamp < RATE_WINDOW
        ]
        self._person_sent[(chat_id, sender_id)] = person
        last = max([*stamps, *self._bot_stamps(chat_id)[-1:]], default=0.0)
        if last and moment - last < float(self.config.reply_cooldown_seconds):
            return "cooldown"
        if len(stamps) >= int(self.config.max_replies_per_10min):
            return "rate_limit"
        if len(person) >= int(self.config.max_person_replies_per_10min):
            return "rate_limit"
        return ""

    async def decide(
        self,
        *,
        chat_id: str,
        text: str,
        is_dm: bool,
        mentioned: bool,
        replied_to_bot: bool,
        sender_id: str,
        has_media: bool,
        quote_sender_is_bot: bool,
        crisis: bool = False,
        has_quote: bool = False,
        now: float | None = None,
    ) -> Decision:
        moment = time.time() if now is None else float(now)
        value, why = self.score(
            chat_id=chat_id,
            text=text,
            is_dm=is_dm,
            mentioned=mentioned,
            replied_to_bot=replied_to_bot,
            sender_id=sender_id,
            has_media=has_media,
            quote_sender_is_bot=quote_sender_is_bot,
            has_quote=has_quote,
            now=moment,
        )
        if crisis or guard.is_crisis(text):
            return self._log(Decision("reply", "crisis", 1.0))
        if not str(text or "").strip() and not has_media:
            return self._log(Decision("ignore", "empty", value))
        blocked = self._over_limit(chat_id, sender_id, moment)
        if blocked:
            return self._log(Decision("ignore", blocked, value))
        if self.config.llm_decide and callable(getattr(self.llm, "decide", None)):
            act = await self._ask(chat_id, text, sender_id)
            if act:
                return self._log(self._apply(act, chat_id, sender_id, is_dm, mentioned, value, "llm"))
        if is_dm or mentioned:
            return self._log(Decision("reply", "direct" if is_dm else "mention", value))
        if value >= float(self.config.reply_threshold):
            return self._log(Decision("reply", f"score:{why}", value))
        if value >= float(self.config.react_threshold) and self.stickers:
            return self._log(Decision("react", f"score:{why}", value))
        if (
            self.config.llm_review
            and not self.config.llm_decide
            and float(self.config.review_low) <= value < float(self.config.review_high)
            and callable(getattr(self.llm, "judge", None))
        ):
            verdict = await self._review(chat_id, text, sender_id, moment)
            if verdict:
                return self._log(self._apply(verdict, chat_id, sender_id, is_dm, mentioned, value, "review"))
        return self._log(Decision("ignore", f"score:{why}", value))

    def _apply(
        self,
        act: str,
        chat_id: str,
        sender_id: str,
        is_dm: bool,
        mentioned: bool,
        value: float,
        source: str,
    ) -> Decision:
        key = (chat_id, sender_id)
        if act == "reply":
            self._ignores[key] = 0
            return Decision("reply", source, value)
        if act == "react":
            if not self.stickers:
                return Decision("ignore", f"{source}:no_sticker", value)
            self._ignores[key] = 0
            return Decision("react", source, value)
        count = self._ignores.get(key, 0) + 1
        self._ignores[key] = count
        if (is_dm or mentioned) and count > int(self.config.max_consecutive_ignores):
            self._ignores[key] = 0
            return Decision("reply", "ignore_cap", value)
        return Decision("ignore", source, value)

    async def _ask(self, chat_id: str, text: str, sender_id: str) -> str:
        from .llm import parse_decision

        user = self._context(chat_id, text, sender_id)
        try:
            raw = await asyncio.wait_for(
                self.llm.decide(DECIDE_PROMPT, user), timeout=DECIDE_TIMEOUT
            )
        except TimeoutError:
            log.warning("keputusan LLM timeout chat=%s", chat_id)
            return ""
        except Exception:  # kegagalan LLM jatuh ke skor deterministik
            log.warning("keputusan LLM gagal chat=%s", chat_id, exc_info=True)
            return ""
        return parse_decision(raw)

    async def _review(self, chat_id: str, text: str, sender_id: str, moment: float) -> str:
        from .llm import parse_decision

        self._reviews = [stamp for stamp in self._reviews if moment - stamp < 60]
        if len(self._reviews) >= int(self.config.max_reviews_per_minute):
            return ""
        self._reviews.append(moment)
        try:
            raw = await asyncio.wait_for(
                self.llm.judge(JUDGE_PROMPT, self._context(chat_id, text, sender_id)),
                timeout=DECIDE_TIMEOUT,
            )
        except TimeoutError:
            log.warning("penilaian LLM timeout chat=%s", chat_id)
            return ""
        except Exception:
            log.warning("penilaian LLM gagal chat=%s", chat_id, exc_info=True)
            return ""
        return parse_decision(raw)

    def _context(self, chat_id: str, text: str, sender_id: str) -> str:
        parts: list[str] = []
        if self.affect is not None:
            try:
                prompt = self.affect.prompt(chat_id, sender_id)
            except Exception:
                prompt = ""
            if prompt:
                parts.append(prompt[:PROMPT_CHARS])
        lines: list[str] = []
        for entry in self._recent(chat_id)[-HISTORY_LINES:]:
            body = " ".join(str(getattr(entry, "text", "") or "").split())
            if not body:
                continue
            who = getattr(entry, "sender_name", "") or getattr(entry, "sender_id", "") or "-"
            lines.append(f"{who}: {body[:HISTORY_CHARS]}")
        if lines:
            parts.append("Percakapan terakhir:\n" + "\n".join(lines))
        sender = sender_id or "-"
        parts.append(f"Pesan terakhir dari {sender}: {' '.join(str(text or '').split())[:HISTORY_CHARS]}")
        return "\n\n".join(parts)

    def record_sent(self, chat_id: str, person_id: str, now: float | None = None) -> None:
        moment = time.time() if now is None else float(now)
        self._sent.setdefault(chat_id, []).append(moment)
        self._person_sent.setdefault((chat_id, person_id), []).append(moment)
        self._ignores[(chat_id, person_id)] = 0

    @staticmethod
    def _log(decision: Decision) -> Decision:
        log.info(
            "gate act=%s score=%.2f reason=%s", decision.act, decision.score, decision.reason
        )
        return decision
