"""Message models shared by the router, policy, and command layers."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class InboundMessage:
    """A normalized inbound WhatsApp message.

    The gateway/normalizer builds this object (plan-3). Core layers only rely on
    these fields, so they can be tested without WhatsApp.
    """

    chat_id: str
    sender_id: str
    text: str
    is_group: bool = False
    mentions: tuple[str, ...] = ()
    reply_to_bot: bool = False
