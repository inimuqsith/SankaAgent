"""Fakta runtime untuk blok Self di prompt dan /status."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

from .config import Paths


def human_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


@dataclass
class RuntimeFacts:
    mode: str = "standby"
    started_at: float = field(default_factory=time.time)
    lease_host: str = ""
    lease_pid: int = 0
    lease_since: float = 0.0
    gateways: tuple[str, ...] = ()
    db_label: str = ""
    memory_status: str = "unavailable"
    pending_approvals: int = 0
    legacy_pending: int = 0
    paths: Paths | None = None

    @property
    def uptime(self) -> str:
        return human_duration(time.time() - self.started_at)

    def lease_label(self) -> str:
        if not self.lease_host or not self.lease_pid:
            return "-"
        since = human_duration(time.time() - self.lease_since) if self.lease_since else "-"
        return f"{self.lease_host} pid {self.lease_pid} since {since} ago"

    def surface(self) -> str:
        return ", ".join(self.gateways) or "-"
