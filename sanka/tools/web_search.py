"""Tool web_search: cari di web lewat beberapa backend ddgs."""

from __future__ import annotations

import asyncio

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _int

WEB_BACKENDS = ("wikipedia", "yahoo", "startpage", "brave")


def _search_sync(query: str, limit: int) -> list[dict]:
    from ddgs import DDGS

    engine = DDGS(timeout=10)
    found: list[dict] = []
    seen: set[str] = set()
    for backend in WEB_BACKENDS:
        try:
            hits = engine.text(query, max_results=limit, backend=backend)
        except Exception:  # satu backend blokir/limit tidak boleh menggagalkan pencarian
            continue
        for hit in hits:
            href = str(hit.get("href") or hit.get("url") or "")
            if href and href in seen:
                continue
            seen.add(href)
            found.append(hit)
        if len(found) >= limit:
            break
    return found[:limit]


async def _web_search(args: dict, ctx: ToolContext) -> str:
    query = str(args.get("query", "")).strip()
    if not query:
        return "error: query kosong"
    limit = min(10, max(1, _int(args, "max_results", 5)))
    results = await asyncio.to_thread(_search_sync, query, limit)
    if not results:
        return "tidak ada hasil"
    return "\n".join(
        f"{item.get('title', '')}\n{item.get('href', '')}\n{item.get('body', '')}\n"
        for item in results
    ).strip()


TOOL = Handler(
    name="web_search",
    family="web_search",
    description="Cari di web dan kembalikan judul, URL, dan cuplikan.",
    fn=_web_search,
    aliases=("search", "cari"),
    parameters={
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Kata kunci pencarian."},
            "max_results": {"type": "integer", "description": "Jumlah hasil 1-10 (default 5)."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
