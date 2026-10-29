"""Teks default untuk `sanka init`; satu sumber kebenaran, tanpa berkas contoh terpisah."""

from __future__ import annotations

import yaml

from ..engine.soul import DEFAULT_SOUL

HOST: dict = {
    "database": {
        "url_env": "SANKA_DATABASE_URL",
        "url": "",
        "pool_size": 5,
        "pool_timeout": 30,
    },
    "workspace": ".sanka/workspace",
    "gateway_file": "gateway.yaml",
    "engine_file": "engine.yaml",
    "roles_dir": "roles",
    "sync_documents": True,
    "sandbox": {
        "runtime": "",
        "image": "docker.io/library/python:3.12-slim",
        "timeout_seconds": 120,
        "network": "none",
        "host_workspace": None,
        "env": [],
    },
    "web_search": {"base_url": "", "timeout_seconds": 10},
    "mcp": {"servers": {}},
}

ENGINE: dict = {
    "llm": {
        "base_url": "https://opencode.ai/inference/go/openai/v1",
        "model": "deepseek-v4.1-flash",
        "api_key_env": "OPENCODE_GO_API_KEY",
        "timeout_seconds": 120,
        "temperature": 0.9,
        "run_budget_seconds": 240,
    },
    "default_role": "guest",
    "reply": {"quiet_seconds": 600},
    "memory": {
        "backend": "mem0",
        "auto_capture": True,
        "capture_min_chars": 8,
        "max_items": 12,
        "max_chars": 3000,
        "collection": "sanka_memories",
        "extraction_model": "",
        "embedder": {
            "provider": "huggingface",
            "model": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2",
            "dimensions": 384,
        },
        "telemetry": False,
    },
    "approval": {
        "mode": "yolo",
        "sensitive_tools": ["shell", "sandbox"],
        "settings": True,
        "ttl_seconds": 900,
    },
    "skills": {"max_index_chars": 3000},
    "prompt": {"salience": True, "max_chars": 9000},
    "humanize": {
        "enabled": True,
        "cpms": 25.0,
        "min_delay": 0.5,
        "max_delay": 5.0,
        "between_delay": 0.7,
        "max_bubbles": 3,
        "reaction_stickers": [],
    },
    "style": {"enabled": True, "max_chars": 800},
    "affect": {
        "enabled": True,
        "volatility": "expressive",
        "grudge_half_life_hours": 1.5,
        "vibe": {
            "enabled": True,
            "model": "Aardiiiiy/EmoSense-ID-Indonesian-Emotion-Classifier",
            "max_length": 128,
            "attack": 0.35,
            "baseline_influence": 0.05,
            "hold_seconds": 1.2,
            "neutral_floor": 0.35,
            "tick_seconds": 1.0,
        },
    },
    "ambient": {
        "enabled": True,
        "tick_seconds": 10,
        "digest_every": 12,
        "digest_cooldown": 900,
        "history_window": 30,
        "idle_minutes": 20,
        "research_cooldown": 21600,
        "research_per_day": 6,
        "research_results": 5,
    },
}

GATEWAY: dict = {
    "gateways": {
        "whatsapp": {
            "enabled": False,
            "session_db": ".sanka/whatsapp.db",
            "backlog_seconds": 600,
        },
        "cli": {
            "enabled": True,
            "role": "owner",
            "user": "local",
            "chat_id": "cli:local",
            "chat_name": "Terminal",
        },
    }
}

SOUL: dict = {"name": "sanka", "language": "id", "pronouns": "aku"}

ROLES: dict[str, dict] = {
    "owner": {
        "name": "owner",
        "description": "Pemilik dan admin Sanka",
        "priority": 100,
        "users": [],
        "tools": ["*"],
        "commands": ["*"],
        "context": (
            "Kamu sedang bicara dengan pemilik Sanka. Dia yang mengatur arah, izin, dan jiwa.\n"
            "Boleh mengoreksi, mengubah config/soul, dan meminta laporan utuh.\n"
        ),
    },
    "member": {
        "name": "member",
        "description": "Panitia",
        "priority": 10,
        "users": [],
        "tools": [
            "read",
            "web_search",
            "chat_history",
            "memory",
            "skill",
            "recipe",
            "send",
            "mcp__*",
        ],
        "commands": [
            "help",
            "ping",
            "reset",
            "whoami",
            "status",
            "tools",
            "cari",
            "memori",
            "skill",
            "kirim",
            "mood",
        ],
        "context": (
            "Kamu sedang bicara dengan anggota panitia. Bantu pekerjaan mereka tanpa membuka izin baru.\n"
        ),
    },
    "guest": {
        "name": "guest",
        "description": "Pengirim tak dikenal",
        "priority": 0,
        "users": [],
        "tools": ["read", "web_search", "chat_history", "memory"],
        "commands": ["help", "ping", "reset", "whoami", "status", "tools", "cari"],
        "context": (
            "Kamu belum mengenal orang ini. Tetap sopan, jangan buka data chat lain.\n"
        ),
    },
}

ENV = """OPENCODE_GO_API_KEY=
SANKA_DATABASE_URL=postgresql+psycopg://sanka:CHANGE_ME@127.0.0.1:5432/sanka
"""


def dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def files() -> dict[str, str]:
    """Semua berkas scaffold: path relatif -> isi."""
    result = {
        "config.yaml": dump(HOST),
        "engine.yaml": dump(ENGINE),
        "gateway.yaml": dump(GATEWAY),
        "soul.yaml": dump(SOUL),
        "SOUL.md": DEFAULT_SOUL,
        ".env": ENV,
    }
    for name, body in ROLES.items():
        result[f"roles/{name}.yaml"] = dump(body)
    return result
