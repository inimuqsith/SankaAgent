"""Agent-facing privacy service: pending confirmations, capture assessment, pre-delivery review.

All level maths and veto precedence live in `memory.privacy.PrivacyPolicy`; this module only
talks to the LLM for drafting a level/question and for spotting which memory items a draft
mentions. Confirmation questions are phrased by the model in the soul's language, with a
short fallback when the LLM is unavailable.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass

from . import guard
from .config import PrivacyConfig
from .memory import MemoryHit
from .memory.privacy import AuditResult, PrivacyPolicy, PrivacyVerdict, clamp_level

log = logging.getLogger(__name__)

KINDS = ("privacy", "disclosure")
YES = ("ya", "yes", "y", "ok", "oke", "boleh", "sip", "gas", "lanjut", "silakan")
NO = ("tidak", "no", "tdk", "gak", "ga", "nggak", "jangan", "engga", "gausah")
LEVEL_WORDS = (
    ("rahasia", 4),
    ("secret", 4),
    ("sensitif", 3),
    ("sensitive", 3),
    ("aib", 3),
    ("personal", 2),
    ("pribadi", 2),
    ("biasa", 2),
    ("umum", 0),
    ("publik", 0),
    ("public", 0),
)

_FALLBACK = {
    "id": {
        "level": (
            "Sepertinya ini privat. Seberapa privat? 0 umum, 1 tim, 2 personal, "
            "3 sensitif, 4 rahasia."
        ),
        "disclosure": "Itu menyentuh hal privat. Boleh aku sebutkan di sini? Balas ya atau tidak.",
        "keep": "🔒 Oke, aku simpan sendiri.",
        "unclear": "Balas ya atau tidak supaya aku tahu.",
        "saved": "🔐 Tercatat di level {level}.",
        "sent": "🔐 Boleh, aku sampaikan.",
        "empty": "🔐 Boleh, tapi tidak ada draft yang bisa dikirim.",
    },
    "en": {
        "level": (
            "This sounds private. How private is it? 0 public, 1 team, 2 personal, "
            "3 sensitive, 4 secret."
        ),
        "disclosure": "That touches something private. May I share it here? Reply yes or no.",
        "keep": "🔒 Okay, I'll keep that to myself.",
        "unclear": "Reply yes or no so I know what to do.",
        "saved": "🔐 Noted at level {level}.",
        "sent": "🔐 Approved, here it is.",
        "empty": "🔐 Approved, but there was no draft to send.",
    },
}

ASSESS_PROMPT = """You label the privacy of one chat turn for a personal assistant.
Reply ONLY with JSON:
{"level":0,"subject":"","ask":false,"question":"","reason":""}
Levels:
- 0 public: anything already shareable with anyone (event info, jokes, general facts).
- 1 team: internal committee logistics that are fine inside the group.
- 2 personal: ordinary personal facts, preferences, habits, light feelings.
- 3 sensitive: genuinely embarrassing content, aib, or something that could damage group
  harmony if repeated.
- 4 secret: a grave secret that must not circulate.
Calibration: most turns are 0-2. Never label ordinary preferences, event details, committee
logistics, jokes, or light teasing as 3/4. Ask only when the level is 4 and the text does not
already state how private it is.
"subject": the person the fact is about, only when their name is explicit in the turn;
otherwise "".
"question": when "ask" is true, one short question in the soul's language asking how private
this should stay; otherwise "".
"reason": at most 80 characters, never quoting a secret."""

ANSWER_PROMPT = """Classify the sender's answer to a pending confirmation.
Reply ONLY with JSON: {"kind":"yes|no|level|unclear","level":0,"reason":""}
- "level" when they name a privacy level (0-4, or public/team/personal/sensitive/secret).
- "yes" when they agree; "no" when they refuse; "unclear" otherwise.
"level" is 0-4 only for kind=level, otherwise 0."""

AUDIT_PROMPT = """You audit one draft reply against memory entries the model was shown.
Reply ONLY with JSON:
{"mentions":["memory-id"],"verdict":"allow|ask|redact|deny","reply":"","question":"","reason":""}
Rules:
- "mentions" lists only the IDs of entries the draft actually uses or paraphrases. Never
  invent IDs; use an empty list when the draft uses none.
- Do not repeat any secret in "reason".
- "reply" may hold a safer rewrite when verdict is "redact"; otherwise "".
- "question": when the deterministic verdicts include "ask", one short question in the
  soul's language asking permission to share; otherwise "".
- The final decision is made deterministically from the entry levels and ownership; your job
  is only mention detection and a safe rewrite."""


@dataclass(frozen=True)
class ReviewResult:
    handled: bool = False
    reply: str = ""


def _json(text: str) -> dict | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        body = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return body if isinstance(body, dict) else None


def _flag(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value or "").strip().lower() in {"true", "yes", "1"}


class PrivacyService:
    def __init__(self, agent) -> None:
        self.agent = agent
        self.configure(agent.config.memory.privacy)

    def configure(self, config: PrivacyConfig) -> None:
        self.config = config
        self.policy = PrivacyPolicy(config)

    # ------------------------------------------------------------- helpers

    @property
    def _lang(self) -> str:
        return "en" if str(self.agent.soul.language) == "en" else "id"

    def _text(self, key: str, **kwargs) -> str:
        template = _FALLBACK[self._lang][key]
        return template.format(**kwargs) if kwargs else template

    def _risky(self, hit: MemoryHit, *, is_dm: bool) -> bool:
        limit = self.config.dm_max_level if is_dm else self.config.group_max_level
        if hit.level > limit or hit.vetoed:
            return True
        return hit.level >= self.config.ask_level and not hit.confirmed

    # -------------------------------------------------------------- review

    async def review(
        self,
        inbound,
        reply: str,
        hits: Sequence[MemoryHit],
        gateway,
    ) -> ReviewResult:
        if not self.config.enabled or not hits:
            return ReviewResult(False, reply)
        is_dm = bool(inbound.is_dm)
        risky = [hit for hit in hits if self._risky(hit, is_dm=is_dm)]
        if not risky:
            return ReviewResult(False, reply)
        if self.config.auditor and self.agent.llm is not None:
            audit = await self._audit(reply, hits, inbound)
        else:
            audit = AuditResult(verdict="ask")
        selected = [hit for hit in hits if hit.id in audit.mentions] or risky
        requester = inbound.person_id or self.agent._sender(inbound)
        verdicts = [
            self.policy.disclose(hit, requester=requester, is_dm=is_dm) for hit in selected
        ]
        if "deny" in verdicts:
            return ReviewResult(False, audit.reply or self._text("keep"))
        if "ask" in verdicts:
            question = self._question(audit)
            approval = await self.agent.approvals.create(
                kind="disclosure",
                chat_id=inbound.chat_id,
                sender=self.agent._sender(inbound),
                gateway=getattr(gateway, "name", "") or "",
                tool="disclosure",
                args={"memory_ids": [hit.id for hit in selected]},
                payload={"reply": reply, "question": question},
                summary=question,
            )
            log.info("disclosure pending chat=%s approval=%s", inbound.chat_id, approval.id)
            await self.agent.send_text(inbound.chat_id, question, gateway=gateway)
            return ReviewResult(True, "")
        return ReviewResult(False, audit.reply or reply)

    def _question(self, audit: AuditResult) -> str:
        question = " ".join(str(audit.question or "").split())
        return question or self._text("disclosure")

    async def _audit(
        self, reply: str, hits: Sequence[MemoryHit], inbound
    ) -> AuditResult:
        requester = inbound.person_id or self.agent._sender(inbound)
        lines = []
        for hit in hits:
            verdict = self.policy.disclose(
                hit, requester=requester, is_dm=bool(inbound.is_dm)
            )
            lines.append(
                f"- id={hit.id} level={hit.level} owner={hit.owner_id or '-'} "
                f"subject={hit.subject_id or '-'} confirmed={hit.confirmed} "
                f"vetoed={hit.vetoed} policy={verdict} text={hit.text}"
            )
        user = (
            f"Draft reply:\n{reply}\n\nMemory entries:\n" + "\n".join(lines)
            + f"\n\nSoul language: {self._lang}"
        )
        raw = await self.agent.llm.complete(AUDIT_PROMPT, user, max_tokens=300)
        body = _json(raw)
        if body is None:
            return AuditResult(verdict="ask")
        mentions = tuple(
            str(item) for item in body.get("mentions") or [] if isinstance(item, str)
        )
        verdict = str(body.get("verdict") or "").strip().lower()
        return AuditResult(
            mentions=mentions,
            verdict=verdict if verdict in {"allow", "ask", "redact", "deny"} else "ask",
            reply=str(body.get("reply") or "").strip(),
            question=str(body.get("question") or "").strip(),
            reason=str(body.get("reason") or "")[:120],
        )

    # ------------------------------------------------------------- capture

    async def capture_turn(self, inbound, reply: str, gateway) -> None:
        message = inbound.message
        text = str(message.text or "").strip()
        if not text or guard.SENSITIVE.search(text):
            return
        if self.config.enabled and self.agent.llm is not None:
            verdict = await self._assess(f"{text}\n{reply}")
        else:
            verdict = PrivacyVerdict(level=self.config.default_level, ask=False)
        owner = inbound.person_id or self.agent._sender(inbound)
        subject = owner
        if verdict.subject and self.agent.affect is not None:
            try:
                subject = self.agent.affect.person_by_name(
                    inbound.chat_id, verdict.subject
                ) or owner
            except Exception:
                log.debug("subject lookup failed", exc_info=True)
        ids = await self.agent.memory.capture(
            [
                {"role": "user", "content": text},
                {"role": "assistant", "content": reply or ""},
            ],
            person_id=owner,
            chat_id=inbound.chat_id,
            audience="private" if inbound.is_dm else "group",
            source="turn",
            level=clamp_level(verdict.level, self.config.default_level),
            owner_id=owner,
            subject_id=subject,
            confirmed=not verdict.ask,
        )
        if not (verdict.ask and ids):
            return
        question = verdict.question or self._text("level")
        approval = await self.agent.approvals.create(
            kind="privacy",
            chat_id=inbound.chat_id,
            sender=self.agent._sender(inbound),
            gateway=getattr(gateway, "name", "") or "",
            tool="memory",
            args={"memory_ids": ids},
            payload={"level": verdict.level, "question": question},
            summary=question,
        )
        log.info("privacy pending chat=%s approval=%s", inbound.chat_id, approval.id)
        await self.agent.send_text(inbound.chat_id, question, gateway=gateway)

    async def _assess(self, text: str) -> PrivacyVerdict:
        user = f"Turn:\n{text}\n\nSoul language: {self._lang}"
        raw = await self.agent.llm.complete(ASSESS_PROMPT, user, max_tokens=160)
        body = _json(raw)
        if body is None:
            return PrivacyVerdict(level=self.config.default_level, ask=False)
        try:
            level = int(body.get("level"))
        except (TypeError, ValueError):
            level = self.config.default_level
        level = clamp_level(level, self.config.default_level)
        ask = _flag(body.get("ask")) and level >= self.config.ask_level
        return PrivacyVerdict(
            level=level,
            subject=" ".join(str(body.get("subject") or "").split())[:60],
            ask=ask,
            question=" ".join(str(body.get("question") or "").split()),
            reason=" ".join(str(body.get("reason") or "").split())[:120],
        )

    # ------------------------------------------------------------- pending

    async def handle_pending(self, inbound, gateway, sender: str) -> bool:
        pending = self.agent.approvals.latest(inbound.chat_id, sender)
        if pending is None or pending.kind not in KINDS:
            return False
        answer = await self._resolve_answer(inbound.message.text, pending.kind)
        if pending.kind == "privacy":
            return await self._resolve_privacy(inbound, gateway, pending, answer, sender)
        return await self._resolve_disclosure(inbound, gateway, pending, answer, sender)

    async def _resolve_privacy(self, inbound, gateway, pending, answer: dict, sender: str) -> bool:
        ids = [str(item) for item in pending.args.get("memory_ids") or []]
        kind = answer.get("kind")
        if kind == "no":
            for memory_id in ids:
                await self.agent.memory.forget(
                    memory_id,
                    requester_id=inbound.person_id or sender,
                    chat_id=inbound.chat_id,
                )
            self.agent.approvals.mark(pending.id, "denied", sender, "privacy declined")
            await self.agent.send_text(inbound.chat_id, self._text("keep"), gateway=gateway)
            return True
        level = answer.get("level")
        if level is None and kind == "yes":
            level = pending.payload.get("level")
        if level is None:
            await self.agent.send_text(inbound.chat_id, self._text("unclear"), gateway=gateway)
            return True
        level = clamp_level(level, self.config.default_level)
        for memory_id in ids:
            await self.agent.memory.set_privacy(memory_id, level=level, confirmed=True)
        self.agent.approvals.mark(pending.id, "approved", sender, f"level {level}")
        await self.agent.send_text(
            inbound.chat_id, self._text("saved", level=level), gateway=gateway
        )
        return True

    async def _resolve_disclosure(
        self, inbound, gateway, pending, answer: dict, sender: str
    ) -> bool:
        kind = answer.get("kind")
        if kind == "no":
            self.agent.approvals.mark(pending.id, "denied", sender, "disclosure declined")
            await self.agent.send_text(inbound.chat_id, self._text("keep"), gateway=gateway)
            return True
        if kind != "yes":
            await self.agent.send_text(inbound.chat_id, self._text("unclear"), gateway=gateway)
            return True
        self.agent.approvals.mark(pending.id, "approved", sender, "disclosure allowed")
        reply = str(pending.payload.get("reply") or "")
        if not reply.strip():
            await self.agent.send_text(inbound.chat_id, self._text("empty"), gateway=gateway)
            return True
        await self.agent.send_text(inbound.chat_id, reply, gateway=gateway)
        return True

    async def _resolve_answer(self, text: str, kind: str) -> dict:
        if self.agent.llm is not None:
            raw = await self.agent.llm.complete(
                ANSWER_PROMPT, f"Pending kind: {kind}\nAnswer:\n{text}", max_tokens=80
            )
            body = _json(raw)
            if body is not None:
                parsed = str(body.get("kind") or "").strip().lower()
                if parsed in {"yes", "no", "level", "unclear"}:
                    level = None
                    if parsed == "level":
                        try:
                            number = int(body.get("level"))
                        except (TypeError, ValueError):
                            number = -1
                        level = number if 0 <= number <= 4 else None
                        if level is None:
                            parsed = "unclear"
                    return {"kind": parsed, "level": level}
        return self._regex_answer(str(text or ""))

    @staticmethod
    def _regex_answer(text: str) -> dict:
        words = set(re.findall(r"[a-z]+", text.lower()))
        for word, level in LEVEL_WORDS:
            if word in words:
                return {"kind": "level", "level": level}
        if words & set(YES):
            return {"kind": "yes", "level": None}
        if words & set(NO):
            return {"kind": "no", "level": None}
        digits = [int(ch) for ch in text if ch.isdigit() and int(ch) <= 4]
        if digits:
            return {"kind": "level", "level": digits[0]}
        return {"kind": "unclear", "level": None}
