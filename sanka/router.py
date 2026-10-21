"""Message routing: pick the command path, agent path, or drop the message."""

from __future__ import annotations

import re
from collections.abc import Iterable
from enum import Enum

from sanka.models import InboundMessage


class Route(str, Enum):
    """Outcome of routing one inbound message."""

    COMMAND = "command"
    AGENT = "agent"
    IGNORE = "ignore"


def route(
    message: InboundMessage,
    *,
    bot_ids: Iterable[str] = (),
    bot_name: str = "sanka",
    require_mention: bool = True,
) -> Route:
    """Decide how a message should be handled.

    Rules (ADR-0003, ADR-0004):
    - A message starting with ``/`` is always a command, in groups too.
    - Blank text is ignored.
    - Private chats send non-command text to the agent.
    - Group chats require a mention, ``@<bot_name>``, or a reply to the bot.

    ``bot_ids`` may contain phone numbers or WhatsApp JIDs; comparison is done
    on digits so ``628...@s.whatsapp.net`` and ``628...`` match.
    """

    text = message.text.strip()
    if not text and not message.attachments and not message.attachment_errors:
        return Route.IGNORE

    if text.startswith("/"):
        return Route.COMMAND

    if not message.is_group:
        return Route.AGENT

    if not require_mention:
        return Route.AGENT

    if message.reply_to_bot:
        return Route.AGENT

    if _calls_bot_name(text, bot_name):
        return Route.AGENT

    if _mentions_bot(message.mentions, bot_ids):
        return Route.AGENT

    return Route.IGNORE


def _calls_bot_name(text: str, bot_name: str) -> bool:
    pattern = rf"(?<![\w@])@{re.escape(bot_name)}(?!\w)"
    return re.search(pattern, text, re.IGNORECASE) is not None


def _mentions_bot(mentions: Iterable[str], bot_ids: Iterable[str]) -> bool:
    raw_ids = set(bot_ids)
    normalized_ids = {_digits(value) for value in raw_ids}
    normalized_ids.discard("")
    for mention in mentions:
        if mention in raw_ids:
            return True
        digits = _digits(mention)
        if digits and digits in normalized_ids:
            return True
    return False


def _digits(value: str) -> str:
    return "".join(char for char in value if char.isdigit())
