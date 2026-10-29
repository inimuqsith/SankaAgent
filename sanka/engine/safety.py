"""SafetyPolicy: gerbang deterministik sebelum tool sensitif (seam untuk robot fisik)."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .config import ApprovalConfig
from .registry import allows

READ_ONLY = frozenset({"read", "memory", "skill", "chat_history", "web_search"})


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    args: dict
    chat_id: str
    sender: str
    gateway: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class SafetyDecision:
    verdict: str  # "allow" | "pending" | "deny"
    reason: str = ""


class SafetyPolicy(Protocol):
    def check(self, request: ActionRequest) -> SafetyDecision: ...


class AllowAllSafety:
    """approval.mode=off: semua tool langsung (dokumentasikan risikonya)."""

    def check(self, request: ActionRequest) -> SafetyDecision:
        return SafetyDecision("allow")


class ApprovalPolicy:
    def __init__(self, config: ApprovalConfig) -> None:
        self.configure(config)

    def configure(self, config: ApprovalConfig) -> None:
        self.config = config

    def check(self, request: ActionRequest) -> SafetyDecision:
        if self.config.mode == "off":
            return SafetyDecision("allow")
        if self.config.mode == "strict" and request.tool not in READ_ONLY:
            return SafetyDecision("pending", "strict mode: semua aksi menunggu /approve")
        if allows(tuple(self.config.sensitive_tools), request.tool):
            return SafetyDecision(
                "pending", f"{request.tool} adalah tool sensitif; butuh /approve"
            )
        return SafetyDecision("allow")


def build_safety(config: ApprovalConfig) -> SafetyPolicy:
    if config.mode == "off":
        return AllowAllSafety()
    return ApprovalPolicy(config)
