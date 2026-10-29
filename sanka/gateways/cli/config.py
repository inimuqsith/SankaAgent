"""Konfigurasi gateway CLI."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CliSettings:
    enabled: bool = False
    role: str = ""
    user: str = "local"
    chat_id: str = "cli:local"
    chat_name: str = "Terminal"

    @classmethod
    def from_mapping(cls, enabled: bool, options: dict) -> "CliSettings":
        return cls(
            enabled=bool(enabled),
            role=str(options.get("role") or ""),
            user=str(options.get("user") or "local"),
            chat_id=str(options.get("chat_id") or "cli:local"),
            chat_name=str(options.get("chat_name") or "Terminal"),
        )
