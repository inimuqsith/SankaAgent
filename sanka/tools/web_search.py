"""Tool web_search: SearXNG bila `web_search.base_url` diisi, fallback ke backend ddgs."""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.parse
import urllib.request

from ..config import Config
from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _int

log = logging.getLogger(__name__)

WEB_BACKENDS = ("wikipedia", "yahoo", "startpage", "brave")
USER_AGENT = "sanka-agent/0.1"


def searx_hits(payload: dict, limit: int) -> list[dict]:
    """JSON SearXNG -> daftar hit seragam (title/href/body); entri tanpa URL dibuang."""
    hits: list[dict] = []
    for item in payload.get("results") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "")
        if not url:
            continue
        hits.append(
            {
                "title": str(item.get("title") or ""),
                "href": url,
                "body": str(item.get("content") or ""),
            }
        )
        if len(hits) >= limit:
            break
    return hits


def _searx_sync(base_url: str, query: str, limit: int, timeout: float) -> list[dict]:
    url = f"{base_url}/search?" + urllib.parse.urlencode({"q": query, "format": "json"})
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return searx_hits(json.load(response), limit)


async def _searx(config: Config, query: str, limit: int) -> list[dict]:
    base_url = config.web_search.base_url
    if not base_url:
        return []
    try:
        return await asyncio.to_thread(_searx_sync, base_url, query, limit, config.web_search.timeout_seconds)
    except Exception as exc:  # instance mati/JSON mati: ddgs jadi fallback, pencarian tetap jalan
        log.warning("searxng %s gagal (%s: %s); fallback ke ddgs", base_url, type(exc).__name__, exc)
        return []


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
    results = await _searx(ctx.config, query, limit)
    if not results:
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
