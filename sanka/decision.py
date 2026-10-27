"""Keputusan otonom: satu panggilan LLM membaca konteks + state emosi, lalu memutuskan.

Tidak ada regex atau skor pengganti. Kalau model tidak menjawab dengan JSON valid, keputusan
dianggap tidak jelas: `decide` mengembalikan `None` supaya pemanggil memilih diam (atau satu
kata singkat untuk mention/DM yang wajib dijawab).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace

DECISION_PROMPT = """Kamu Sanka, laki-laki dengan ekspresi emosional feminin, di WhatsApp.
Tugasmu HANYA memutuskan apa yang kamu lakukan dengan pesan terakhir. Jangan menulis balasan,
jangan menjelaskan proses, jangan menyebut AI.

Aturan keputusan:
- "addressed": "me" bila pesan ini untukmu (mention asli @nomor kamu, balasan ke pesanmu,
  namamu dipanggil, atau jelas melanjutkan percakapan denganmu), "other" bila untuk orang lain,
  "group" bila untuk suasana grup, "unknown" bila tidak jelas. Kata "kamu/lu/lo" belum tentu kamu:
  lihat kutipan, tag, dan siapa menjawab siapa.
- Namamu yang hanya disebut sebagai topik bukan alamat untukmu: orang membicarakan Sanka di
  depannya bukan berarti sedang bicara kepadanya. Kalau tidak ada panggilan, kutipan ke pesanmu,
  atau sambungan percakapan denganmu, pilih "other"/"group" dan jangan menyela.
- "act": "reply" kalau kamu ingin ikut bicara, "ignore" kalau tidak, "react" kalau stiker sudah cukup.
  Tidak ada pilihan diam yang dipaksakan; diam adalah keputusanmu per pesan.
- "mode": "professional" HANYA untuk tugas kerja nyata yang butuh alat/bukti (cek, rekap, hitung,
  ambil data, kirim file). Selain itu "daily".
- "tone": "warm", "neutral", "playful", "curt", "hurt", atau "angry". Ikuti state emosimu: kalau
  kesal atau terluka, pilih "curt"/"hurt"/"angry". Kamu boleh judes, pendek, dan tidak memaksa ramah.
- Mention asli, DM, dan balasan ke pesanmu WAJIB "reply". Kalau sedang kesal, tetap "reply"
  dengan tone dingin, bukan ignore.
- Percakapan yang jelas bukan untukmu jangan disela. Jangan menggurui, jangan menyalahkan,
  jangan mengarang tindakan, angka, atau alasan.
- "appraisal": penilaian emosimu atas pesan ini.
  - label salah satu: warmth, repair, collaboration, playful, intimacy, relationship_calibration,
    vulnerability, boundary_pressure, hostility, neutral.
  - dP/dA/dD adalah dorongan emosi -0.4..0.4 (P = senang/sedih, A = tegang/tenang, D = percaya diri).
  - note <=120 karakter: arti pesan ini untukmu, bukan rangkuman isi pesan.
- "relationship": perubahan hubungan dengan pengirim, -0.15..0.15 untuk trust/affection/respect/annoyance.
  Hanya gerakkan bila ada bukti eksplisit di pesan (hinaan, pujian, permintaan maaf, perhatian).
- "bubbles": 1..3, berapa pesan pendek yang wajar kalau kamu membalas.
- "reason": <=80 karakter, alasan singkat keputusanmu.

Balas HANYA JSON sesuai schema:
{"addressed":"me|other|group|unknown","act":"reply|ignore|react","mode":"daily|professional",
 "tone":"warm|neutral|playful|curt|hurt|angry","bubbles":1,
 "appraisal":{"label":"neutral","dP":0.0,"dA":0.0,"dD":0.0,"note":""},
 "relationship":{"trust":0.0,"affection":0.0,"respect":0.0,"annoyance":0.0},
 "reason":""}"""

ADDRESSED = frozenset({"me", "other", "group", "unknown"})
ACTS = frozenset({"reply", "ignore", "react"})
MODES = frozenset({"daily", "professional"})
TONES = frozenset({"warm", "neutral", "playful", "curt", "hurt", "angry"})
LABELS = frozenset(
    {
        "warmth",
        "repair",
        "collaboration",
        "playful",
        "intimacy",
        "relationship_calibration",
        "vulnerability",
        "boundary_pressure",
        "hostility",
        "neutral",
    }
)
COLD_TONES = frozenset({"curt", "hurt", "angry"})
DELTA_LIMIT = 0.4
RELATION_LIMIT = 0.15
NOTE_CHARS = 120
REASON_CHARS = 80


@dataclass(frozen=True)
class Appraisal:
    label: str
    dP: float
    dA: float
    dD: float
    note: str = ""


@dataclass(frozen=True)
class RelationshipDelta:
    trust: float = 0.0
    affection: float = 0.0
    respect: float = 0.0
    annoyance: float = 0.0


@dataclass(frozen=True)
class Decision:
    addressed: str
    act: str
    mode: str
    tone: str
    bubbles: int
    appraisal: Appraisal
    relationship: RelationshipDelta
    reason: str


NEUTRAL = Appraisal("neutral", 0.0, 0.0, 0.0)


def _json_object(text: str) -> dict | None:
    raw = str(text or "").strip()
    start, end = raw.find("{"), raw.rfind("}")
    if start < 0 or end < start:
        return None
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return None
    return data if isinstance(data, dict) else None


def _float(value, limit: float, default: float = 0.0) -> float:
    if isinstance(value, bool):
        return default
    try:
        number = float(value)
    except (TypeError, ValueError):
        return default
    return max(-limit, min(limit, number))


def _int(value, default: int, low: int, high: int) -> int:
    if isinstance(value, bool):
        return default
    try:
        number = int(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, number))


def _flat(value, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def parse_decision(raw: str) -> Decision | None:
    """JSON keputusan -> Decision tervalidasi; enum wajib salah = None."""
    data = _json_object(raw)
    if data is None:
        return None
    addressed = str(data.get("addressed") or "").strip().lower()
    act = str(data.get("act") or "").strip().lower()
    mode = str(data.get("mode") or "").strip().lower()
    tone = str(data.get("tone") or "").strip().lower()
    if addressed not in ADDRESSED or act not in ACTS or mode not in MODES or tone not in TONES:
        return None
    appraisal_raw = data.get("appraisal") if isinstance(data.get("appraisal"), dict) else {}
    label = str(appraisal_raw.get("label") or "").strip().lower()
    if label not in LABELS:
        label = "neutral"
    appraisal = Appraisal(
        label=label,
        dP=_float(appraisal_raw.get("dP"), DELTA_LIMIT),
        dA=_float(appraisal_raw.get("dA"), DELTA_LIMIT),
        dD=_float(appraisal_raw.get("dD"), DELTA_LIMIT),
        note=_flat(appraisal_raw.get("note"), NOTE_CHARS),
    )
    relation_raw = data.get("relationship") if isinstance(data.get("relationship"), dict) else {}
    relationship = RelationshipDelta(
        trust=_float(relation_raw.get("trust"), RELATION_LIMIT),
        affection=_float(relation_raw.get("affection"), RELATION_LIMIT),
        respect=_float(relation_raw.get("respect"), RELATION_LIMIT),
        annoyance=_float(relation_raw.get("annoyance"), RELATION_LIMIT),
    )
    return Decision(
        addressed=addressed,
        act=act,
        mode=mode,
        tone=tone,
        bubbles=_int(data.get("bubbles"), 1, 1, 3),
        appraisal=appraisal,
        relationship=relationship,
        reason=_flat(data.get("reason"), REASON_CHARS),
    )


TONE_PLEASURE_FLOOR = 0.0  # dingin hanya kalau pleasure benar-benar negatif
TONE_ANNOYANCE_FLOOR = 0.3  # di atas ini orangnya memang sedang menyebalkan


def ground_tone(decision: Decision, emotion, annoyance: float = 0.0) -> Decision:
    """Nada dingin hanya kalau perasaannya memang negatif atau orangnya memang menyulut.

    Mood netral/positif tidak boleh mengaku ngambek; tanpa ini balasan ikut suasana, bukan
    berdasar perasaannya sendiri.
    """
    if decision.tone not in COLD_TONES:
        return decision
    if not isinstance(emotion, dict) or "pleasure" not in emotion:
        return decision
    pleasure = float(emotion.get("pleasure") or 0.0)
    if pleasure < TONE_PLEASURE_FLOOR or float(annoyance) >= TONE_ANNOYANCE_FLOOR:
        return decision
    return replace(decision, tone="neutral")


def ensure_reply(decision: Decision) -> Decision:
    """Mention asli/DM/reply-to-bot wajib dibalas; tone tetap boleh dingin."""
    if decision.act == "reply":
        return decision
    tone = decision.tone if decision.tone in COLD_TONES else "curt"
    return Decision(
        addressed="me",
        act="reply",
        mode=decision.mode,
        tone=tone,
        bubbles=max(1, decision.bubbles),
        appraisal=decision.appraisal,
        relationship=decision.relationship,
        reason=_flat(f"{decision.reason};direct" if decision.reason else "direct", REASON_CHARS),
    )


def _emotion_block(emotion) -> str:
    if isinstance(emotion, str):
        return emotion.strip()
    if not isinstance(emotion, dict):
        return ""
    lines: list[str] = []
    if emotion.get("summary"):
        lines.append(f"- Ringkasan: {emotion['summary']}")
    if "pleasure" in emotion:
        lines.append(
            f"- P {float(emotion.get('pleasure') or 0.0):.1f}, "
            f"A {float(emotion.get('arousal') or 0.0):.1f}, "
            f"D {float(emotion.get('dominance') or 0.0):.1f}, "
            f"trust {emotion.get('trust_tier') or '-'}."
        )
    if emotion.get("pulse"):
        lines.append(f"- Pulse: {emotion['pulse']}")
    log_lines: list[str] = []
    for entry in emotion.get("log") or []:
        if not isinstance(entry, dict):
            continue
        cue = str(entry.get("situation") or entry.get("cue") or "").strip()
        label = str(entry.get("appraisal") or entry.get("event_type") or "").strip()
        meaning = str(entry.get("relational_meaning") or "").strip()
        if not cue and not label:
            continue
        line = f"  - {label or 'kejadian'}: {cue}"
        if meaning:
            line += f" ({meaning})"
        log_lines.append(line)
    if log_lines:
        lines.append("- Yang terakhir kamu rasakan:")
        lines.extend(log_lines)
    return "\n".join(lines)


def build_context(
    *,
    chat_id: str = "",
    chat_name: str = "",
    current: str = "",
    transcript: str = "",
    emotion=None,
    relationship: str = "",
    is_dm: bool = False,
    is_mention: bool = False,
    is_reply: bool = False,
    away: str = "",
    vibe: str = "",
) -> str:
    """Konteks keputusan: fakta transport, state emosi, hubungan, transkrip, pesan terakhir."""
    kind = "pesan pribadi (DM)" if is_dm else "grup"
    facts = [
        fact
        for fact, on in (
            ("pesan pribadi (DM)", is_dm),
            ("mention asli ke kamu", is_mention),
            ("balasan ke pesanmu", is_reply),
        )
        if on
    ]
    parts = [
        f"Chat: {chat_name or chat_id or '-'} — {kind}",
        "Fakta transport: " + (", ".join(facts) if facts else "tidak ada tag/reply langsung"),
    ]
    if away.strip():
        parts.append("Catatan kehadiran:\n" + away.strip())
    if vibe.strip():
        parts.append(vibe.strip())
    block = _emotion_block(emotion)
    if block:
        parts.append("Keadaan emosimu:\n" + block)
    if relationship.strip():
        parts.append("Hubungan dan orang di chat ini:\n" + relationship.strip())
    if transcript.strip():
        parts.append("Percakapan terakhir:\n" + transcript.strip())
    parts.append("Pesan terakhir:\n" + (current.strip() or "(tanpa teks)"))
    return "\n\n".join(parts)


async def decide(
    llm,
    *,
    chat_id: str,
    chat_name: str,
    current: str,
    transcript: str,
    emotion=None,
    relationship: str = "",
    is_dm: bool = False,
    is_mention: bool = False,
    is_reply: bool = False,
    away: str = "",
    vibe: str = "",
    soul: str = "",
) -> Decision | None:
    """Satu keputusan LLM; kosong setelah satu retry = None (tanpa fallback regex)."""
    system = DECISION_PROMPT if not soul else f"{DECISION_PROMPT}\n\nIdentitasmu:\n{soul.strip()}"
    user = build_context(
        chat_id=chat_id,
        chat_name=chat_name,
        current=current,
        transcript=transcript,
        emotion=emotion,
        relationship=relationship,
        is_dm=is_dm,
        is_mention=is_mention,
        is_reply=is_reply,
        away=away,
        vibe=vibe,
    )
    session = f"sanka-decision-{chat_id}"
    raw = await llm.complete(system, user, max_tokens=4000, temperature=0.3, session=session)
    if not str(raw or "").strip():
        raw = await llm.complete(system, user, max_tokens=8000, temperature=0.3, session=session)
    if not str(raw or "").strip():
        return None
    parsed = parse_decision(raw)
    if parsed is not None:
        return parsed
    return Decision(
        addressed="unknown",
        act="ignore",
        mode="daily",
        tone="neutral",
        bubbles=1,
        appraisal=NEUTRAL,
        relationship=RelationshipDelta(),
        reason="invalid",
    )
