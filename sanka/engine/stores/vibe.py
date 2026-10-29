"""Vibe ruangan: dinamika attack/hold/decay ala anima dengan classifier emosi Indonesia.

Bukan pengganti `affect`: vibe adalah mood ruangan yang bergerak terus dari teks, disimpan
per chat di tabel `state`, dan dipakai sebagai konteks prompt. Semua kegagalan fail-open.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import math
import time
from dataclasses import dataclass, field

from sqlalchemy import select

from ..config import VibeConfig
from ..db import Database
from ..schema import state as state_table

log = logging.getLogger(__name__)

NEUTRAL = (0.5, 0.4, 0.5)
CURRENT_TAU = (120.0, 45.0, 90.0)  # V, A, D detik
BASELINE_TAU = 14400.0  # 4 jam
EMOTION_VAD = {
    "anger": (0.15, 0.85, 0.80),
    "anticipation": (0.60, 0.60, 0.55),
    "disgust": (0.10, 0.55, 0.60),
    "fear": (0.20, 0.85, 0.25),
    "joy": (0.90, 0.65, 0.65),
    "sadness": (0.15, 0.30, 0.25),
    "surprise": (0.55, 0.85, 0.45),
    "trust": (0.78, 0.40, 0.60),
}

STATE_KEY = "vibe"


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


@dataclass
class VibeState:
    """Mood sekarang (current) dan mood dasar yang bergerak lambat (baseline)."""

    current: list[float] = field(default_factory=lambda: list(NEUTRAL))
    baseline: list[float] = field(default_factory=lambda: list(NEUTRAL))
    hold_until: float = 0.0
    last_tick: float = 0.0


def advance(state: VibeState, now: float, config: VibeConfig) -> None:
    """Satu detak: current kembali ke baseline, baseline kembali ke netral."""
    dt = max(0.0, now - state.last_tick) if state.last_tick > 0 else 0.0
    state.last_tick = now
    if dt <= 0 or now < state.hold_until:
        return
    for index in range(3):
        factor = 1.0 - math.exp(-dt / CURRENT_TAU[index])
        state.current[index] += (state.baseline[index] - state.current[index]) * factor
    factor = 1.0 - math.exp(-dt / BASELINE_TAU)
    for index in range(3):
        state.baseline[index] += (NEUTRAL[index] - state.baseline[index]) * factor
    state.current = [_clamp(value) for value in state.current]
    state.baseline = [_clamp(value) for value in state.baseline]


class VibeRunner:
    """Classifier EmoSense-ID + dinamika vibe per chat; heartbeat menjaga decay tetap jalan."""

    def __init__(self, db: Database, config: VibeConfig, classifier=None):
        self.config = config
        self._db = db
        self._classifier = classifier
        self._states: dict[str, VibeState] = self._load()
        self._lock = asyncio.Lock()
        self._running = False
        self._task: asyncio.Task | None = None
        self._tokenizer = None
        self._model = None
        self._torch = None
        self._failed = False

    # ------------------------------------------------------------------ lifecycle

    async def start(self) -> bool:
        """True bila vibe siap dipakai; False berarti nonaktif (fail-open, tanpa exception)."""
        if self._running:
            return True
        if self._classifier is None and self._model is None:
            log.info("vibe: memuat model %s", self.config.model)
            try:
                await asyncio.to_thread(self._load_model)
            except Exception:
                log.warning("vibe: model %s gagal dimuat; vibe nonaktif", self.config.model)
                log.debug("vibe: detail gagal muat model", exc_info=True)
                self._failed = True
                return False
        self._running = True
        self._task = asyncio.create_task(self._heartbeat())
        return True

    def _load_model(self) -> None:
        import torch
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self._torch = torch
        self._tokenizer = AutoTokenizer.from_pretrained(self.config.model)
        self._model = AutoModelForSequenceClassification.from_pretrained(self.config.model)
        self._model.eval()

    def _classify(self, text: str) -> dict[str, float]:
        inputs = self._tokenizer(
            text,
            return_tensors="pt",
            truncation=True,
            max_length=int(self.config.max_length),
        )
        with self._torch.no_grad():
            logits = self._model(**inputs).logits[0]
            probs = self._torch.softmax(logits, dim=-1).tolist()
        labels = getattr(self._model.config, "id2label", None) or {}
        if not labels:
            raise RuntimeError("model tanpa id2label")
        return {str(labels[index]).strip().lower(): float(value) for index, value in enumerate(probs)}

    async def close(self) -> None:
        self._running = False
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None
        self._save()

    async def _heartbeat(self) -> None:
        interval = max(0.2, float(self.config.tick_seconds))
        while self._running:
            await asyncio.sleep(interval)
            async with self._lock:
                now = time.time()
                for state in self._states.values():
                    advance(state, now, self.config)

    # ------------------------------------------------------------------ per chat

    @staticmethod
    def vad(probs: dict | None, floor: float) -> list[float] | None:
        """Distribusi label -> satu titik VAD; di bawah `floor` dianggap netral (None)."""
        if not probs or max(probs.values()) < floor:
            return None
        total = 0.0
        acc = [0.0, 0.0, 0.0]
        for label, mass in probs.items():
            point = EMOTION_VAD.get(str(label).strip().lower())
            if point is None:
                continue
            total += float(mass)
            for index in range(3):
                acc[index] += float(mass) * point[index]
        if total <= 0:
            return None
        return [_clamp(value / total) for value in acc]

    async def observe(self, chat_id: str, text: str) -> str:
        """Satu pesan memengaruhi vibe chat ini; mengembalikan blok prompt yang siap dipakai."""
        if not str(text or "").strip() or not self._running or self._failed:
            return ""
        try:
            if self._classifier is not None:
                probs = self._classifier(text)
            else:
                probs = await asyncio.to_thread(self._classify, text)
            target = self.vad(probs, float(self.config.neutral_floor))
            if target is None:
                return await self.describe(chat_id)
            async with self._lock:
                state = self._state(chat_id)
                now = time.time()
                advance(state, now, self.config)
                for index in range(3):
                    state.current[index] += (
                        target[index] - state.current[index]
                    ) * float(self.config.attack)
                for index in range(3):
                    state.baseline[index] += (
                        state.current[index] - state.baseline[index]
                    ) * float(self.config.baseline_influence)
                state.current = [_clamp(value) for value in state.current]
                state.baseline = [_clamp(value) for value in state.baseline]
                state.hold_until = now + float(self.config.hold_seconds)
                return self._describe(state)
        except Exception:
            log.warning("vibe: gagal memproses chat=%s", chat_id)
            log.debug("vibe: detail kegagalan", exc_info=True)
            self._failed = True
            return ""

    async def describe(self, chat_id: str) -> str:
        async with self._lock:
            return self._describe(self._state(chat_id))

    def _describe(self, state: VibeState) -> str:
        # Band dibaca dari `current` (mood cepat), bukan `baseline` yang butuh jam untuk bergeser.
        v, a, d = state.current
        mood = "negatif" if v < 0.35 else ("netral" if v < 0.6 else "positif")
        energy = "tenang" if a < 0.35 else ("sedang" if a < 0.65 else "berenergi")
        power = "tertekan" if d < 0.4 else ("seimbang" if d < 0.65 else "percaya diri")
        return (
            f"Room vibe: {mood}, {energy}, {power}. "
            "Ini konteks, bukan izin menyerang; jangan sebut angka atau istilah teknis."
        )

    # ------------------------------------------------------------------ persistence

    def _state(self, chat_id: str) -> VibeState:
        state = self._states.get(chat_id)
        if state is None:
            state = VibeState()
            self._states[chat_id] = state
        return state

    def _load(self) -> dict[str, VibeState]:
        try:
            row = self._db.fetch_one(
                select(state_table.c.value).where(state_table.c.key == STATE_KEY)
            )
            raw = json.loads(row["value"]) if row is not None else None
        except Exception:
            log.warning("vibe: gagal memuat state dari database", exc_info=True)
            return {}
        if not isinstance(raw, dict):
            return {}
        states: dict[str, VibeState] = {}
        for chat_id, body in raw.items():
            if not isinstance(body, dict):
                continue
            current = self._triple(body.get("current"))
            baseline = self._triple(body.get("baseline"))
            if current is None or baseline is None:
                continue
            states[str(chat_id)] = VibeState(current=current, baseline=baseline)
        return states

    @staticmethod
    def _triple(value) -> list[float] | None:
        if not isinstance(value, list) or len(value) != 3:
            return None
        try:
            return [_clamp(float(item)) for item in value]
        except (TypeError, ValueError):
            return None

    def _save(self) -> None:
        body = {
            chat_id: {"current": state.current, "baseline": state.baseline}
            for chat_id, state in self._states.items()
        }
        try:
            with self._db.connect() as conn:
                self._db.upsert(
                    conn,
                    state_table,
                    [
                        {
                            "key": STATE_KEY,
                            "value": json.dumps(body),
                            "updated_at": time.time(),
                        }
                    ],
                    ["key"],
                )
        except Exception:
            log.warning("vibe: gagal menyimpan state ke database", exc_info=True)
