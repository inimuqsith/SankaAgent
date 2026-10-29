"""Ablasi read-only: replay aturan balasan, anggaran prompt, dan pergeseran keputusan.

Harness ini tidak memanggil `handle_message`, tidak mengirim pesan, dan tidak menulis
state runtime. Semua waktu diambil dari `message.when` supaya replay deterministik.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass

from . import salience
from .context import ChatMessage, prompt_entry
from .config import Config
from .decision import decide
from .prompts import system_prompt
from .roles import Role

REPLY_WINDOW = 1
TRANSCRIPT_LINES = 30
ABLATABLE = ("emotion", "vibe", "away")


@dataclass(frozen=True)
class Row:
    message: ChatMessage
    called: bool
    replied: bool


def classify(messages: list[ChatMessage], *, bot_name: str) -> list[Row]:
    """Replay gerbang balasan dari arsip; satu baris per pesan masuk, kronologis.

    Arsip tidak menyimpan mention asli, jadi `called` hanya dari kutipan ke bot: batas
    bawah (lower bound) dari gerbang runtime yang juga melihat mention asli.
    """
    rows: list[Row] = []
    pending = -1  # indeks baris pesan masuk yang masih menunggu balasan
    pending_at = -1  # posisinya di arsip, untuk jendela balasan
    for position, message in enumerate(messages):
        if message.outgoing:
            if pending < 0 or position - pending_at > REPLY_WINDOW:
                continue
            rows[pending] = dataclasses.replace(rows[pending], replied=True)
            pending = -1
            continue
        called = message.quote is not None and message.quote.sender_name == bot_name
        rows.append(Row(message, called, False))
        pending = len(rows) - 1
        pending_at = position
    return rows


def replay_metrics(rows: list[Row]) -> dict:
    """Hitungan gerbang: panggilan, balasan, dan kesalahan arah."""
    incoming = len(rows)
    false_positive = sum(1 for row in rows if row.replied and not row.called)
    return {
        "incoming": incoming,
        "called": sum(1 for row in rows if row.called),
        "replied": sum(1 for row in rows if row.replied),
        "false_positive": false_positive,
        "missed": sum(1 for row in rows if row.called and not row.replied),
        "unprompted_rate": false_positive / incoming if incoming else 0.0,
    }


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def prompt_budget(
    config: Config,
    messages: list[ChatMessage],
    *,
    sample: int,
    blocks_for,
) -> dict:
    """Ukuran system prompt sebelum/sesudah kompetisi blok, plus frekuensi blok dibuang."""
    incoming = [message for message in messages if not message.outgoing]
    if sample > 0:
        incoming = incoming[-sample:]
    stripped = dataclasses.replace(
        config, prompt=dataclasses.replace(config.prompt, salience=False)
    )
    mandatory = {
        "base": "x",
        "self": "",
        "roles": "",
        "safety": "",
        "away": "",
        "emotion": "x",
        "vibe": "x",
        "memory": "",
    }
    before: list[int] = []
    after: list[int] = []
    dropped: dict[str, int] = {}
    for message in incoming:
        source = blocks_for(message)
        blocks = {
            name: source.get(name, "")
            for name in (*salience.COMPETING, "memory")
        }
        before.append(len(system_prompt(stripped, (Role(name="member"),), query=message.text, **blocks)))
        after.append(
            len(system_prompt(config, (Role(name="member"),), query=message.text, bonuses={}, **blocks))
        )
        _, names = salience.select(
            {**mandatory, **blocks},
            message.text,
            bonuses={},
            max_chars=config.prompt.max_chars,
            enabled=config.prompt.salience,
        )
        for name in names:
            dropped[name] = dropped.get(name, 0) + 1
    return {
        "samples": len(incoming),
        "before_mean": _mean(before),
        "after_mean": _mean(after),
        "saved_mean": _mean(before) - _mean(after),
        "dropped": dropped,
    }


def emotion_brief(affect, chat_id: str) -> str | dict:
    """Ringkasan read-only state emosi; `""` bila state belum ada atau tidak terbaca.

    `status()` bisa menulis state default saat file belum ada, jadi harness hanya membacanya
    kalau folder state sudah berisi state nyata.
    """
    if affect is None:
        return ""
    try:
        status = affect.status(chat_id)
    except (OSError, ValueError, KeyError):
        return ""
    return {
        key: status[key] for key in ("summary", "pleasure", "arousal", "dominance", "trust_tier")
    }


def _transcript(history: list[ChatMessage], target: ChatMessage, bot_name: str) -> str:
    index = next((i for i, item in enumerate(history) if item is target), len(history))
    window = history[max(0, index - TRANSCRIPT_LINES) : index]
    return "\n".join(prompt_entry(item, bot_name, 500) for item in window)


async def decision_shift(
    llm,
    config: Config,
    rows: list[Row],
    *,
    sample: int,
    ablate: tuple[str, ...],
    chat_id: str = "",
    history: list[ChatMessage] | None = None,
    affect=None,
) -> dict:
    """Bandingkan keputusan gate dengan dan tanpa blok yang diabsi (LLM sungguhan)."""
    if sample <= 0:
        return {
            "samples": 0,
            "addressed_changed": 0,
            "act_changed": 0,
            "tone_changed": 0,
            "examples": [],
        }
    sampled = rows[-sample:]
    messages = history if history is not None else [row.message for row in rows]
    bot_name = config.soul.name
    is_dm = bool(chat_id) and not chat_id.endswith("@g.us")
    counts = {"addressed_changed": 0, "act_changed": 0, "tone_changed": 0}
    compared = 0
    examples: list[dict] = []
    for row in sampled:
        message = row.message
        current = prompt_entry(message, bot_name, 500)
        transcript = _transcript(messages, message, bot_name)
        common = {
            "chat_id": chat_id,
            "chat_name": "",
            "current": current,
            "transcript": transcript,
            "relationship": "",
            "is_dm": is_dm,
            "is_mention": False,
            "is_reply": message.quote is not None and message.quote.sender_name == bot_name,
            "away": "" if "away" in ablate else "catatan",
            "vibe": "" if "vibe" in ablate else "suasana hangat",
            "soul": config.soul.persona,
            "language": config.soul.language,
        }
        emotion = emotion_brief(affect, chat_id)
        before = await decide(llm, emotion=emotion, **common)
        after = await decide(llm, emotion="" if "emotion" in ablate else emotion, **common)
        if before is None or after is None:
            continue
        compared += 1
        changed = {
            "addressed_changed": before.addressed != after.addressed,
            "act_changed": before.act != after.act,
            "tone_changed": before.tone != after.tone,
        }
        for key, hit in changed.items():
            counts[key] += int(hit)
        if any(changed.values()) and len(examples) < 3:
            examples.append(
                {
                    "text": message.text,
                    "before": {
                        "addressed": before.addressed,
                        "act": before.act,
                        "tone": before.tone,
                    },
                    "after": {
                        "addressed": after.addressed,
                        "act": after.act,
                        "tone": after.tone,
                    },
                }
            )
    return {"samples": compared, **counts, "examples": examples}
