"""Gender target dari cara orang mengetik, avatar hanya sebagai cadangan.

Tidak ada klasifikasi wajah: sinyal utamanya marker bahasa di jendela terakhir pesan
pengirim itu sendiri (bot tidak ikut memilih). Ambangnya sengaja konservatif --- kalau
ragu hasilnya `unknown`, dan target `unknown` tidak pernah membuka kekasaran. Avatar hanya
dibaca kalau gaya bicara tidak memberi jawaban dan host menyalakannya.
"""

from __future__ import annotations

import re

MALE_MARKERS = (
    "gue",
    "gua",
    "gw",
    "bro",
    "bray",
    "cuy",
    "woi",
    "jir",
    "anjir",
    "njir",
    "goblok",
    "bangsat",
    "kontol",
    "bgst",
    "bang",
)
FEMALE_MARKERS = (
    "akuu",
    "gemes",
    "gemoy",
    "kak",
    "kaka",
    "cantiq",
    "bestie",
    "girl",
    "deh",
    "sihh",
    "kawaii",
    "😝",
    "🥺",
    "💅",
    "🙈",
)
STYLE_WINDOW = 8
STYLE_MIN_HITS = 3
STYLE_MARGIN = 0.6
GENDERS = ("male", "female", "unknown", "")

GENDER_WORDS = {
    "male": "male",
    "m": "male",
    "pria": "male",
    "cowok": "male",
    "laki": "male",
    "laki-laki": "male",
    "lk": "male",
    "female": "female",
    "f": "female",
    "wanita": "female",
    "cewek": "female",
    "perempuan": "female",
    "pr": "female",
    "unknown": "unknown",
    "?": "unknown",
    "netral": "unknown",
    "none": "unknown",
}


def _marker_count(text: str, marker: str) -> int:
    """Kata utuh untuk marker alfabet (biar 'bang' tidak menangkap 'banget'), substring untuk emoji."""
    if marker.isalpha():
        return len(re.findall(rf"\b{re.escape(marker)}\b", text))
    return text.count(marker)


def score_style(messages: list[str]) -> tuple[str, float]:
    """(label, keyakinan) dari jendela pesan pengirim; ragu = ("unknown", 0.0)."""
    window = [str(item or "").lower()[-400:] for item in messages if str(item or "").strip()]
    body = "\n".join(window[-STYLE_WINDOW:])
    if not body:
        return ("unknown", 0.0)
    male = sum(_marker_count(body, marker.lower()) for marker in MALE_MARKERS)
    female = sum(_marker_count(body, marker.lower()) for marker in FEMALE_MARKERS)
    margin = (male - female) / max(1, male + female)
    if male >= STYLE_MIN_HITS and margin >= STYLE_MARGIN:
        return ("male", round(abs(margin), 2))
    if female >= STYLE_MIN_HITS and -margin >= STYLE_MARGIN:
        return ("female", round(abs(margin), 2))
    return ("unknown", 0.0)


def combine(
    style: tuple[str, float],
    avatar: tuple[str, float] | None,
    *,
    gate: bool = True,
) -> tuple[str, float, str]:
    """Gaya menang; avatar hanya cadangan saat gaya ragu. Hasil: (label, keyakinan, sumber)."""
    label, confidence = style
    if label in ("male", "female"):
        return (label, float(confidence), "style")
    if gate and avatar is not None and avatar[0] in ("male", "female"):
        return (avatar[0], float(avatar[1]), "avatar")
    return ("unknown", float(confidence), "none")


def parse_gender(value: str) -> str | None:
    """Argumen /gender -> label kanonik; None kalau bukan label yang dikenal."""
    key = " ".join(str(value or "").strip().lower().split())
    return GENDER_WORDS.get(key)


AVATAR_PROMPT = """You look at one profile picture from an Indonesian chat app and label the
person's apparent gender: male, female, or unknown.
Rules:
- Answer with one word only: male, female, or unknown.
- An unknown face, a cartoon, a group logo, or anything ambiguous is unknown.
- Never use names, clothes alone, or a guess you cannot see. Unknown is a fine answer."""


async def classify(llm, raw: bytes, mime: str) -> tuple[str, float] | None:
    """Avatar -> (label, 0.6) atau None; hanya sinyal cadangan, gagal = unknown."""
    import base64

    if llm is None or not raw or not mime:
        return None
    payload = [
        {"type": "text", "text": "Label this profile picture."},
        {
            "type": "image_url",
            "image_url": {"url": f"data:{mime};base64,{base64.b64encode(raw).decode('ascii')}"},
        },
    ]
    try:
        answer = await llm.complete(
            AVATAR_PROMPT,
            payload,
            max_tokens=8,
            temperature=0.0,
            reasoning_effort="minimal",
            session="sanka-avatar",
        )
    except (OSError, RuntimeError, ValueError):  # endpoint tanpa vision: tetap unknown
        return None
    label = str(answer or "").strip().lower().split()
    if not label:
        return None
    found = label[0].strip(".,!:;")
    return (found, 0.6) if found in ("male", "female") else None
