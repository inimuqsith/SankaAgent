"""Loop LLM OpenAI-compatible dengan tool calling dan batas jumlah langkah."""

from __future__ import annotations

import json
import logging

from openai import APIError, AsyncOpenAI

from .config import Config
from .registry import Handler
from .tools import ToolContext, execute

log = logging.getLogger(__name__)

TURN_LIMIT_TEXT = "Batas langkah agent tercapai. Coba lagi atau persempit permintaannya."
API_ERROR_TEXT = "Maaf, model sedang error. Coba lagi sebentar."
USER_AGENT = "sanka-agent/0.1"
IMAGE_LOST = "[Gambar gagal dilampirkan ke model; gunakan path di atas dengan sandbox.]"
MEMORY_KINDS = frozenset({"fact", "preference", "rule"})
REFLECT_PROMPT = """Kamu mesin ekstraksi memori untuk asisten WhatsApp kepanitiaan.
Dari konteks percakapan, memori aktif, pesan pengguna, dan jawaban asisten, ambil 0-3 fakta, preferensi, atau aturan singkat yang EKSPLISIT dan TAHAN LAMA.
Balas HANYA JSON array, tanpa teks lain:
[{"scope":"chat"|"global","kind":"fact"|"preference"|"rule","text":"...","supersedes":<id>}]
Aturan:
- scope "global" hanya untuk info kepanitiaan yang relevan lintas grup; "chat" untuk info grup ini.
- kind "rule" untuk aturan singkat yang eksplisit (mis. "rapat selalu Senin"), bukan prosedur bertahap.
- Prosedur bertahap yang layak diulang bukan memori; itu bahan skill, jadi jangan tulis di sini.
- "supersedes" opsional: isi ID memori aktif yang DIGANTIKAN teks baru (scope dan kind wajib sama).
  Isi hanya bila fakta lama benar-benar diganti/dikoreksi; fakta yang berdiri sendiri jangan disentuh.
  Jika ragu, jangan isi supersedes.
- Jangan simpan rahasia, kredensial, tebakan, status sementara, atau isi yang tidak dinyatakan pengguna.
- Jangan jadikan jawaban asisten sebagai fakta; hanya simpan yang dikonfirmasi/eksplisit dari pengguna.
- Jika tidak ada yang layak, balas []."""


def _id(value) -> int | None:
    """ID dari JSON model: hanya integer positif (bool dan string aneh ditolak)."""
    if isinstance(value, bool):
        return None
    try:
        number = int(value)
    except (TypeError, ValueError):
        return None
    return number if number > 0 else None


def parse_memory_items(text: str) -> list[dict]:
    """Parse JSON array hasil refleksi; item invalid dibuang, maksimal 3."""
    raw = str(text or "").strip()
    start, end = raw.find("["), raw.rfind("]")
    if start < 0 or end < start:
        return []
    try:
        data = json.loads(raw[start : end + 1])
    except json.JSONDecodeError:
        return []
    items: list[dict] = []
    for item in data if isinstance(data, list) else []:
        if not isinstance(item, dict):
            continue
        scope = str(item.get("scope") or "").strip().lower()
        kind = str(item.get("kind") or "").strip().lower()
        body = " ".join(str(item.get("text") or "").split())
        if scope not in {"chat", "global"} or kind not in MEMORY_KINDS or not body:
            continue
        entry = {"scope": scope, "kind": kind, "text": body}
        supersedes = _id(item.get("supersedes"))
        if supersedes is not None:
            entry["supersedes"] = supersedes
        items.append(entry)
        if len(items) >= 3:
            break
    return items


def _has_images(content: str | list[dict]) -> bool:
    return isinstance(content, list) and any(
        isinstance(part, dict) and part.get("type") == "image_url" for part in content
    )


def _client_error(exc: APIError) -> bool:
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and 400 <= status < 500


def _text_only(content: str | list[dict]) -> str:
    if isinstance(content, str):
        return content
    texts = [str(part.get("text", "")) for part in content if isinstance(part, dict) and part.get("type") == "text"]
    return "\n".join([*texts, IMAGE_LOST])


class LLM:
    def __init__(self, config: Config):
        self.config = config
        self.client = AsyncOpenAI(
            base_url=config.llm.base_url,
            api_key=config.api_key(),
            timeout=config.llm.timeout_seconds,
            default_headers={"User-Agent": USER_AGENT},
        )

    def _tools(self, specs: list[Handler]) -> list[dict] | None:
        if not specs:
            return None
        return [
            {
                "type": "function",
                "function": {
                    "name": spec.name,
                    "description": spec.description,
                    "parameters": spec.parameters,
                },
            }
            for spec in specs
        ]

    async def run(
        self,
        system: str,
        user_content: str | list[dict],
        specs: list[Handler],
        ctx: ToolContext,
        session: str = "",
    ) -> str:
        messages: list[dict] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user_content},
        ]
        images = _has_images(user_content)
        by_name = {spec.name: spec for spec in specs}
        tools = self._tools(specs)
        for _ in range(max(1, self.config.llm.max_turns)):
            kwargs = {
                "model": self.config.llm.model,
                "messages": messages,
                "extra_headers": {"x-opencode-session": session or USER_AGENT},
            }
            if tools:
                kwargs["tools"] = tools
            try:
                response = await self.client.chat.completions.create(**kwargs)
            except APIError as exc:
                if images and _client_error(exc):  # model tanpa vision: buang gambar, ulangi sekali
                    log.warning("model menolak gambar, ulang tanpa gambar: %s", exc)
                    messages[1] = {"role": "user", "content": _text_only(user_content)}
                    images = False
                    continue
                log.exception("LLM gagal")
                return API_ERROR_TEXT
            except TimeoutError:
                log.exception("LLM gagal")
                return API_ERROR_TEXT
            message = response.choices[0].message
            calls = message.tool_calls or []
            if not calls:
                return (message.content or "").strip()
            messages.append(message.model_dump(exclude_none=True))
            for call in calls:
                name = call.function.name
                log.info("tool: %s", name)
                try:
                    args = json.loads(call.function.arguments or "{}")
                except json.JSONDecodeError:
                    result = "error: argumen JSON tidak valid"
                else:
                    result = await execute(by_name, name, args if isinstance(args, dict) else {}, ctx)
                messages.append({"role": "tool", "tool_call_id": call.id, "content": result})
        return TURN_LIMIT_TEXT

    async def reflect(
        self, user_text: str, answer: str, context: str = "", memory: str = ""
    ) -> list[dict]:
        """Ekstrak memori tahan lama dari satu pertukaran; kegagalan tidak mengganggu bot."""
        text = str(user_text or "").strip()
        if not text:
            return []
        parts = []
        if context := str(context or "").strip():
            parts.append(f"Konteks percakapan terbaru:\n{context}")
        if memory := str(memory or "").strip():
            parts.append(f"Memori aktif (ID valid untuk supersedes):\n{memory}")
        parts.append(f"Pesan pengguna sekarang:\n{text}")
        parts.append(f"Jawaban asisten:\n{str(answer or '').strip()}")
        messages = [
            {"role": "system", "content": REFLECT_PROMPT},
            {"role": "user", "content": "\n\n".join(parts)},
        ]
        try:
            response = await self.client.chat.completions.create(
                model=self.config.llm.model,
                messages=messages,
                extra_headers={"x-opencode-session": f"{USER_AGENT}-reflect"},
            )
        except (APIError, TimeoutError, OSError) as exc:  # refleksi bersifat best-effort
            log.warning("refleksi memori gagal: %s", exc)
            return []
        return parse_memory_items(response.choices[0].message.content or "")
