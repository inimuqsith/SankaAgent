"""Kompetisi blok prompt ala global workspace: yang wajib selalu masuk, sisanya bersaing."""

from __future__ import annotations

from .context import keyword_set

MANDATORY = (
    "base",
    "character",
    "research",
    "self",
    "roles",
    "safety",
    "away",
    "emotion",
    "vibe",
    "memory",
    "actions",
)
COMPETING = ("topic", "currents", "style", "person_style", "skills")
ORDER = (
    "base",
    "character",
    "research",
    "self",
    "roles",
    "safety",
    "away",
    "emotion",
    "vibe",
    "topic",
    "currents",
    "style",
    "person_style",
    "memory",
    "actions",
    "skills",
)
JOIN = "\n\n"


def block_score(text: str, query: str, bonus: float) -> float:
    """0..1.3: 0.7 * (irisan kata kunci / 3, maks 1) + bonus blok."""
    overlap = len(keyword_set(text) & keyword_set(query))
    return 0.7 * min(1.0, overlap / 3.0) + float(bonus)


def select(
    blocks: dict[str, str],
    query: str = "",
    *,
    bonuses: dict[str, float] | None = None,
    max_chars: int = 9000,
    enabled: bool = True,
) -> tuple[list[tuple[str, str]], list[str]]:
    """(blok terpilih berurutan sesuai ORDER, nama blok yang dibuang)."""
    present = {name: text for name, text in blocks.items() if text.strip()}
    kept = [name for name in ORDER if name in present]
    dropped: list[str] = []
    if not enabled or int(max_chars) <= 0:
        return [(name, present[name]) for name in kept], dropped
    bonus_map = bonuses or {}
    while len(JOIN.join(present[name] for name in kept)) > int(max_chars):
        candidates = [name for name in kept if name in COMPETING]
        if not candidates:
            break  # blok wajib tidak pernah dibuang
        weakest = min(
            candidates,
            key=lambda name: (
                block_score(present[name], query, bonus_map.get(name, 0.0)),
                ORDER.index(name),
            ),
        )
        kept.remove(weakest)
        dropped.append(weakest)
    return [(name, present[name]) for name in kept], dropped
