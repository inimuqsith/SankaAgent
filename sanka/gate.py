"""Gate balasan: Sanka sendiri yang memutuskan reply/react/ignore dan jumlah bubble.

Keputusan utama datang dari `LLM.decide` (sadar mood, drive, dan hubungan); tag/DM tidak bisa
di-ignore. Skor deterministik di bawah adalah prior/fallback, dipakai saat LLM mati, saat
`llm_decide` dimatikan, dan untuk memutuskan chime otonom di `ambient.py`. Tidak ada kuota,
cooldown, atau jeda paksa: hanya penilaian model/skor yang membuat Sanka diam, plus
`max_decisions_per_minute` sebagai pagar biaya. Setiap keputusan juga membawa `mode`
(`daily`/`professional`) yang menentukan gaya kerja dan format laporan di `wa.py`.
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
from dataclasses import dataclass

from . import guard
from .chat import ChatMessage, prompt_entry

log = logging.getLogger(__name__)

DECIDE_PROMPT = """Kamu Sanka, laki-laki dengan ekspresi emosional feminin, di grup WhatsApp.
Baca percakapan, siapa yang bicara, hubungan, dan pesan terakhir. Kamu punya jiwa: keputusanmu
boleh berubah karena mood, keakraban, dan apa yang baru terjadi. Tentukan tindakanmu sendiri.
Balas HANYA JSON: {"act":"reply|react|ignore","bubbles":1,"mode":"daily|professional","why":"<=80 char"}
- reply: kamu mau menanggapi.
- react: cukup reaksi singkat/stiker; tidak perlu jawaban.
- ignore: kamu pilih diam; percakapan bukan untukmu, atau tidak perlu.
- bubbles: 1 untuk balasan biasa. Pakai 2-3 hanya bila perlu beberapa pesan terpisah; jangan
  menambah bubble untuk mengulang atau memancing.
- mode: "daily" atau "professional". Default "daily".
  * daily: obrolan, sapaan, ledekan, pendapat, pertanyaan ringan yang bisa dijawab dari chat/memori.
  * professional: pengirim meminta kamu mengerjakan, memeriksa, atau membuat sesuatu dengan alat
    (cek server, jalankan perintah, baca/unduh file, rekap data, kirim file, analisis), atau
    jawabannya butuh kerja nyata dan bukti. Contoh: "cek status server via shell" -> professional;
    "cp apa sih" -> daily.
- mode tidak mengubah izin, sikap, atau keputusan balas; kamu tetap Sanka.
- Baris "kamu (Sanka)" adalah pesanmu sendiri. Kalau baru mengirim beberapa pesan, pilih 1.
- Kalau pesan men-tag atau membalas kamu, jangan pilih ignore.
- Kalau orang lain menyebut namamu atau membicarakanmu tanpa men-tag, boleh reply/react;
  pilih ignore kalau tidak ada nilai.
- Tentukan dulu siapa yang diajak bicara. "kamu/lu/lo" belum tentu kamu; cek pengirim,
  kutipan, tag, dan siapa yang baru bicara.
- Anggap untukmu hanya bila pesan men-tag/membalas pesanmu, memanggil namamu (termasuk
  variasi memanjang/typo seperti SANKAAAAAAAAA, SANNNKAAAAAA, sankaaaaaa), atau jelas
  melanjutkan percakapan denganmu.
- Sindiran/ledekan yang objeknya orang lain bukan untukmu. Contoh: "pantes bego" yang
  bukan balasan/panggilan untukmu -> ignore/react. "ilham cuekin aja" yang memerintahkanmu
  mengabaikan Ilham -> untukmu.
- Stiker/emoji tanpa kata bukan bukti kamu atau orang lain sedang ngambek. Stiker kucing
  menunduk bisa berarti malu, tunduk, minta maaf, atau bercanda; jangan menuduh ngambek
  tanpa kata-kata.
- Kamu tidak punya tubuh fisik. Jangan mengarang kondisi tubuh (kaki pegal, badan capek,
  sakit).
- Di chat pribadi (DM) semua pesan untukmu; tag, kutipan, atau namamu tidak diperlukan.
Pertimbangkan mood, drive (gabut/kesepian/energi), hubungan, dan siapa yang aktif.
Jangan menjelaskan proses, jangan menyebut AI."""

JUDGE_PROMPT = """Kamu penilai percakapan grup WhatsApp. Bot bernama "Sanka" baru saja menerima pesan terakhir.
Tugasmu: apakah Sanka perlu membalas?
Balas HANYA satu kata: reply, ignore, atau react.
Aturan:
- reply: pesan ditujukan ke Sanka, bertanya, minta bantuan, mengoreksi, atau melanjutkan obrolan dengannya.
- react: cukup reaksi singkat/stiker; tidak perlu jawaban.
- ignore: percakapan antar manusia, ledekan yang tidak butuh jawaban, spam, atau Sanka baru saja menjawab.
- Pesan yang membalas Sanka tetapi hanya komentar ke orang lain → ignore.
- Tentukan dulu siapa yang diajak bicara. "kamu/lu/lo" belum tentu Sanka; pesan hanya untuk Sanka
  bila men-tag/membalas pesannya, memanggil namanya (termasuk variasi memanjang/typo), atau jelas
  melanjutkan percakapan dengannya.
- Sindiran/ledekan yang objeknya orang lain bukan untuk Sanka.
- Stiker/emoji tanpa kata bukan bukti ngambek atau marah; jangan mengarang isi visual atau kondisi
  tubuh yang tidak diberikan.
- Di chat pribadi (DM) semua pesan untuk Sanka; tag atau namanya tidak diperlukan."""

ACTS = frozenset({"reply", "react", "ignore"})
SECOND_PERSON = re.compile(r"\b(kamu|lu|lo|kau|km|kmu|sanka|san)\b", re.IGNORECASE)
REQUEST = re.compile(r"\b(tolong|coba|bantu|carikan|jelaskan|rekap|kirim|buat)\b", re.IGNORECASE)
WORK = re.compile(
    r"\b(tolong|coba|bantu|carikan|jelaskan|rekap|kirim|buat|cek|check|jalankan|eksekusi|"
    r"install|deploy|restart|perbaiki|hapus|ubah|hitung|analisis|scan|download|upload|uji|"
    r"debug|status|log)\b",
    re.IGNORECASE,
)
HISTORY_LINES = 12
HISTORY_CHARS = 300
PROMPT_CHARS = 700
RECENT_WINDOW = 600.0
DECIDE_TIMEOUT = 8.0


def mode_for(text: str) -> str:
    """Tebakan mode saat model tidak memberi mode: permintaan kerja -> professional."""
    return "professional" if WORK.search(str(text or "")) else "daily"


@dataclass(frozen=True)
class Decision:
    act: str
    reason: str
    score: float
    bubbles: int = 1
    mode: str = ""


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
        self._decisions: list[float] = []
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
        named: bool = False,
        now: float | None = None,
    ) -> tuple[float, str]:
        """Skor deterministik 0..1 (prior/fallback) beserta alasan yang bisa dibaca log."""
        moment = time.time() if now is None else float(now)
        body = str(text or "")
        points = 0.0
        tags: list[str] = []
        if named:
            points += 0.45
            tags.append("named")
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
        if (
            recent
            and not mentioned
            and not named
            and all(not getattr(item, "outgoing", False) for item in recent)
        ):
            points -= 0.20
            tags.append("human_flow")
        if contentless(body, has_media):
            points -= 0.20
            tags.append("contentless")
        if len(body.strip()) < 4:
            points -= 0.15
            tags.append("short")
        window = [stamp for stamp in stamps if moment - stamp < RECENT_WINDOW]
        if len(window) >= 3:
            points -= 0.10
            tags.append("chatty")
        return max(0.0, min(1.0, points)), ",".join(tags) or "casual"

    # ------------------------------------------------------------------ keputusan

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
        named: bool = False,
        sender_name: str = "",
        current: ChatMessage | None = None,
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
            named=named,
            now=moment,
        )
        if crisis or guard.is_crisis(text):
            return self._log(Decision("reply", "crisis", 1.0))
        if not str(text or "").strip() and not has_media:
            return self._log(Decision("ignore", "empty", value))

        direct = is_dm or mentioned

        act, bubbles, mode = "", 1, ""
        if self.config.llm_decide and callable(getattr(self.llm, "decide", None)):
            act, bubbles, mode = await self._ask(
                chat_id,
                text,
                sender_id,
                sender_name,
                named,
                moment,
                current=current,
                is_dm=is_dm,
                mentioned=mentioned,
                replied_to_bot=replied_to_bot,
                has_quote=has_quote,
                quote_sender_is_bot=quote_sender_is_bot,
            )
        mode = mode or mode_for(text)

        if direct:
            if act in {"", "ignore"}:  # tag/DM tidak bisa diabaikan; sulk diurus wa.py
                act = "reply"
            if act == "react" and not self.stickers:
                act = "reply"
            return self._log(
                Decision(act, "direct" if is_dm else "mention", value, min(bubbles, 2), mode)
            )

        if act:
            return self._log(self._apply(act, bubbles, value, "llm", chat_id, mode))

        if contentless(text, has_media):
            return self._log(Decision("ignore", "contentless", value, mode=mode))
        if value >= float(self.config.reply_threshold):
            return self._log(Decision("reply", f"score:{why}", value, 1, mode))
        if value >= float(self.config.react_threshold) and self.stickers:
            return self._log(Decision("react", f"score:{why}", value, 1, mode))
        if (
            self.config.llm_review
            and not self.config.llm_decide
            and float(self.config.review_low) <= value < float(self.config.review_high)
            and callable(getattr(self.llm, "judge", None))
        ):
            verdict = await self._review(
                chat_id,
                text,
                sender_id,
                sender_name,
                named,
                moment,
                current=current,
                is_dm=is_dm,
                mentioned=mentioned,
                replied_to_bot=replied_to_bot,
                has_quote=has_quote,
                quote_sender_is_bot=quote_sender_is_bot,
            )
            if verdict:
                return self._log(self._apply(verdict, 1, value, "review", chat_id, mode))
        return self._log(Decision("ignore", f"score:{why}", value, mode=mode))

    def _apply(
        self,
        act: str,
        bubbles: int,
        value: float,
        source: str,
        chat_id: str,
        mode: str = "daily",
    ) -> Decision:
        """Keputusan model -> Decision; reaksi tanpa stiker tidak dikirim sama sekali."""
        if act == "react" and not self.stickers:
            log.info("gate react tanpa stiker chat=%s", chat_id)
            return Decision("ignore", f"{source}:no_sticker", value, mode=mode)
        return Decision(act, source, value, max(1, int(bubbles)), mode)

    async def _ask(
        self,
        chat_id: str,
        text: str,
        sender_id: str,
        sender_name: str,
        named: bool,
        moment: float,
        *,
        current: ChatMessage | None = None,
        is_dm: bool = False,
        mentioned: bool = False,
        replied_to_bot: bool = False,
        has_quote: bool = False,
        quote_sender_is_bot: bool = False,
    ) -> tuple[str, int, str]:
        from .llm import parse_verdict

        self._decisions = [stamp for stamp in self._decisions if moment - stamp < 60]
        if len(self._decisions) >= int(self.config.max_decisions_per_minute):
            return "", 1, ""
        self._decisions.append(moment)
        user = self._context(
            chat_id,
            text,
            sender_id,
            sender_name,
            named,
            moment,
            current=current,
            is_dm=is_dm,
            mentioned=mentioned,
            replied_to_bot=replied_to_bot,
            has_quote=has_quote,
            quote_sender_is_bot=quote_sender_is_bot,
        )
        try:
            raw = await asyncio.wait_for(
                self.llm.decide(DECIDE_PROMPT, user), timeout=DECIDE_TIMEOUT
            )
        except TimeoutError:
            log.warning("keputusan LLM timeout chat=%s", chat_id)
            return "", 1, ""
        except Exception:  # kegagalan LLM jatuh ke skor deterministik
            log.warning("keputusan LLM gagal chat=%s", chat_id, exc_info=True)
            return "", 1, ""
        return parse_verdict(raw)

    async def _review(
        self,
        chat_id: str,
        text: str,
        sender_id: str,
        sender_name: str,
        named: bool,
        moment: float,
        *,
        current: ChatMessage | None = None,
        is_dm: bool = False,
        mentioned: bool = False,
        replied_to_bot: bool = False,
        has_quote: bool = False,
        quote_sender_is_bot: bool = False,
    ) -> str:
        from .llm import parse_verdict

        self._reviews = [stamp for stamp in self._reviews if moment - stamp < 60]
        if len(self._reviews) >= int(self.config.max_reviews_per_minute):
            return ""
        self._reviews.append(moment)
        try:
            raw = await asyncio.wait_for(
                self.llm.judge(
                    JUDGE_PROMPT,
                    self._context(
                        chat_id,
                        text,
                        sender_id,
                        sender_name,
                        named,
                        moment,
                        current=current,
                        is_dm=is_dm,
                        mentioned=mentioned,
                        replied_to_bot=replied_to_bot,
                        has_quote=has_quote,
                        quote_sender_is_bot=quote_sender_is_bot,
                    ),
                ),
                timeout=DECIDE_TIMEOUT,
            )
        except TimeoutError:
            log.warning("penilaian LLM timeout chat=%s", chat_id)
            return ""
        except Exception:
            log.warning("penilaian LLM gagal chat=%s", chat_id, exc_info=True)
            return ""
        return parse_verdict(raw)[0]

    def _context(
        self,
        chat_id: str,
        text: str,
        sender_id: str,
        sender_name: str,
        named: bool,
        moment: float,
        *,
        current: ChatMessage | None = None,
        is_dm: bool = False,
        mentioned: bool = False,
        replied_to_bot: bool = False,
        has_quote: bool = False,
        quote_sender_is_bot: bool = False,
    ) -> str:
        """Konteks keputusan: siapa, mood/drive/hubungan, dan percakapan yang sudah ada."""
        parts: list[str] = [
            "Chat: pesan pribadi (DM), semua pesan di sini untukmu."
            if is_dm
            else "Chat: grup."
        ]
        if self.affect is not None:
            try:
                prompt = self.affect.prompt(chat_id, sender_id)
            except Exception:
                prompt = ""
            if prompt:
                parts.append(prompt[:PROMPT_CHARS])
        lines: list[str] = []
        for entry in self._recent(chat_id)[-HISTORY_LINES:]:
            line = prompt_entry(entry, self.bot_name, HISTORY_CHARS)
            if line:
                lines.append(line)
        if lines:
            parts.append("Percakapan terakhir:\n" + "\n".join(lines))
        bot = [stamp for stamp in self._bot_stamps(chat_id) if moment - stamp < RECENT_WINDOW]
        last = max(bot, default=0.0)
        status = f"Status: pesan terakhirmu {int(max(0, moment - last))} detik lalu"
        status += f"; {len(bot)} pesanmu dalam 10 menit terakhir."
        parts.append(status)
        name = " ".join(str(sender_name or "").split()) or "-"
        parts.append(f"Pengirim sekarang: {name} ({sender_id or '-'})")
        facts: list[str] = []
        if mentioned:
            facts.append("men-tag kamu")
        if replied_to_bot or (has_quote and quote_sender_is_bot):
            facts.append("membalas pesanmu")
        elif has_quote:
            facts.append("membalas pesan orang lain, bukan pesanmu")
        if named:
            facts.append("menyebut namamu (bisa variasi memanjang/typo)")
        if facts:
            parts.append("Arah pesan: " + "; ".join(facts) + ".")
        last_line = (
            prompt_entry(current, self.bot_name, HISTORY_CHARS)
            if current is not None
            else f"{name}: {' '.join(str(text or '').split())[:HISTORY_CHARS]}"
        )
        parts.append("Pesan terakhir: " + (last_line or "(kosong)"))
        return "\n\n".join(parts)

    @staticmethod
    def _log(decision: Decision) -> Decision:
        log.info(
            "gate act=%s mode=%s score=%.2f bubbles=%d reason=%s",
            decision.act,
            decision.mode or "-",
            decision.score,
            decision.bubbles,
            decision.reason,
        )
        return decision
