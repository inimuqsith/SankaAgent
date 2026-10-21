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
        history: list[dict],
        user_text: str,
        specs: list[Handler],
        ctx: ToolContext,
        session: str = "",
    ) -> str:
        messages: list[dict] = [
            {"role": "system", "content": system},
            *history,
            {"role": "user", "content": user_text},
        ]
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
            except (APIError, TimeoutError):
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
