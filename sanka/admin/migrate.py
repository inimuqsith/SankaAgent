"""`sanka config migrate`: pecah config.yaml lama menjadi host/engine/gateway/soul/roles."""

from __future__ import annotations

import time
from pathlib import Path

import yaml

from ..engine.config import ConfigError
from ..engine.soul import infer_language
from . import templates

# Snapshot izin handler lama (field Handler.defaults) supaya role file eksplisit.
LEGACY_DEFAULTS: dict[str, dict[str, list[str]]] = {
    "owner": {
        "tools": [
            "chat_history",
            "mcp",
            "memory",
            "read",
            "recipe",
            "sandbox",
            "send",
            "send_to",
            "shell",
            "skill",
            "web_search",
            "write",
        ],
        "commands": [
            "backfill",
            "calmdown",
            "cari",
            "help",
            "kirim",
            "memori",
            "mood",
            "ping",
            "reset",
            "skill",
            "status",
            "stop",
            "tools",
            "whoami",
        ],
    },
    "member": {
        "tools": [
            "chat_history",
            "mcp",
            "memory",
            "read",
            "recipe",
            "send",
            "skill",
            "web_search",
        ],
        "commands": [
            "cari",
            "help",
            "kirim",
            "memori",
            "mood",
            "ping",
            "reset",
            "skill",
            "status",
            "tools",
            "whoami",
        ],
    },
}

PRIORITY = {"owner": 100, "member": 10, "guest": 0}

NEW_FILES = ("engine.yaml", "gateway.yaml", "soul.yaml")
DROPPED = (
    "llm.max_turns",
    "whatsapp.group_reply",
    "whatsapp.thread_seconds",
    "memory.path",
    "skills.dir",
    "recipes.*",
    "affect.path",
    "affect.state_dir",
    "affect.vibe.state_path",
    "style.path",
    "ambient.proactive",
    "ambient.chime_*",
)


def _section(raw: dict, key: str) -> dict:
    body = raw.get(key)
    return dict(body) if isinstance(body, dict) else {}


def _host(raw: dict) -> dict:
    host = dict(templates.HOST)
    host["workspace"] = str(raw.get("workspace") or host["workspace"])
    if isinstance(raw.get("sandbox"), dict):
        sandbox = dict(templates.HOST["sandbox"])
        sandbox.update({k: v for k, v in raw["sandbox"].items() if k in sandbox})
        host["sandbox"] = sandbox
    if isinstance(raw.get("web_search"), dict):
        search = dict(templates.HOST["web_search"])
        search.update({k: v for k, v in raw["web_search"].items() if k in search})
        host["web_search"] = search
    if isinstance(raw.get("mcp"), dict):
        host["mcp"] = raw["mcp"]
    return host


def _engine(raw: dict, default_role: str) -> dict:
    engine = dict(templates.ENGINE)
    llm = dict(templates.ENGINE["llm"])
    old_llm = _section(raw, "llm")
    for key in llm:
        if key in old_llm:
            llm[key] = old_llm[key]
    engine["llm"] = llm
    engine["default_role"] = default_role
    wa = _section(raw, "whatsapp")
    engine["reply"] = {"quiet_seconds": int(wa.get("quiet_seconds") or 600)}

    memory = dict(templates.ENGINE["memory"])
    old_memory = _section(raw, "memory")
    for key in ("auto_capture", "max_items", "max_chars"):
        if key in old_memory:
            memory[key] = old_memory[key]
    if "auto_extract" in old_memory:
        memory["auto_capture"] = old_memory["auto_extract"]
    engine["memory"] = memory

    skills = dict(templates.ENGINE["skills"])
    old_skills = _section(raw, "skills")
    if "max_index_chars" in old_skills:
        skills["max_index_chars"] = old_skills["max_index_chars"]
    engine["skills"] = skills

    prompt = dict(templates.ENGINE["prompt"])
    prompt.update({k: v for k, v in _section(raw, "prompt").items() if k in prompt})
    engine["prompt"] = prompt

    humanize = dict(templates.ENGINE["humanize"])
    humanize.update({k: v for k, v in _section(raw, "humanize").items() if k in humanize})
    engine["humanize"] = humanize

    style = dict(templates.ENGINE["style"])
    style.update({k: v for k, v in _section(raw, "style").items() if k in style})
    engine["style"] = style

    affect = dict(templates.ENGINE["affect"])
    old_affect = _section(raw, "affect")
    for key in ("enabled", "volatility", "grudge_half_life_hours"):
        if key in old_affect:
            affect[key] = old_affect[key]
    vibe = dict(templates.ENGINE["affect"]["vibe"])
    vibe.update({k: v for k, v in _section(old_affect, "vibe").items() if k in vibe})
    affect["vibe"] = vibe
    engine["affect"] = affect

    ambient = dict(templates.ENGINE["ambient"])
    ambient.update({k: v for k, v in _section(raw, "ambient").items() if k in ambient})
    engine["ambient"] = ambient
    return engine


def _gateway(raw: dict, default_role: str) -> dict:
    wa = _section(raw, "whatsapp")
    gateway = {
        "gateways": {
            "whatsapp": {
                "enabled": True,
                "session_db": str(wa.get("session_db") or ".sanka/whatsapp.db"),
                "backlog_seconds": int(wa.get("backlog_seconds") or 600),
            },
            "cli": {
                "enabled": False,
                "role": default_role,
                "user": "local",
                "chat_id": "cli:local",
                "chat_name": "Terminal",
            },
        }
    }
    return gateway


def _soul(raw: dict, base: Path) -> dict:
    wa = _section(raw, "whatsapp")
    persona = ""
    path = base / "SOUL.md"
    if path.is_file():
        try:
            persona = path.read_text(encoding="utf-8")
        except OSError:
            persona = ""
    return {
        "name": str(wa.get("bot_name") or "sanka"),
        "language": infer_language(persona),
        "pronouns": "aku",
    }


def _roles(raw: dict) -> dict[str, str]:
    old = raw.get("roles")
    if not isinstance(old, dict) or not old:
        raise ConfigError("config lama tidak punya 'roles' untuk dimigrasikan")
    files: dict[str, str] = {}
    for name, body in old.items():
        body = body if isinstance(body, dict) else {}
        defaults = LEGACY_DEFAULTS.get(str(name), {"tools": [], "commands": []})
        tools = body.get("tools")
        commands = body.get("commands")
        role = {
            "name": str(name),
            "description": str(body.get("description") or f"Role {name}"),
            "priority": int(PRIORITY.get(str(name), 1)),
            "users": list(body.get("users") or []),
            "tools": list(defaults["tools"] if tools is None else tools),
            "commands": list(defaults["commands"] if commands is None else commands),
            "context": str(body.get("context") or ""),
        }
        files[f"roles/{name}.yaml"] = templates.dump(role)
    return files


def plan(folder: str | Path) -> tuple[dict[str, str], list[str]]:
    """Hitung isi berkas baru tanpa menulis; kembalikan (files, catatan kunci usang)."""
    base = Path(folder).expanduser().resolve()
    target = base / "config.yaml"
    if not target.is_file():
        raise ConfigError(f"config.yaml tidak ditemukan: {target}")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config.yaml tidak valid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml kosong atau bukan mapping")
    if "whatsapp" not in raw and "roles" not in raw:
        raise ConfigError("config.yaml bukan format lama (tidak ada 'whatsapp'/'roles')")
    default_role = str(raw.get("default_role") or "")
    if default_role not in (raw.get("roles") or {}):
        raise ConfigError(f"default_role '{default_role}' tidak ada di roles lama")
    files = {
        "config.yaml": templates.dump(_host(raw)),
        "engine.yaml": templates.dump(_engine(raw, default_role)),
        "gateway.yaml": templates.dump(_gateway(raw, default_role)),
        "soul.yaml": templates.dump(_soul(raw, base)),
        **_roles(raw),
    }
    return files, list(DROPPED)


def migrate(
    folder: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[dict[str, str], list[str], Path | None]:
    """Tulis hasil migrasi; backup config.yaml lama. Kembalikan (files, dropped, backup)."""
    base = Path(folder).expanduser().resolve()
    files, dropped = plan(base)
    if not force:
        existing = [
            name
            for name in (*NEW_FILES, "roles")
            if (base / name).exists()
        ]
        if existing:
            raise ConfigError(
                "config baru sudah ada: "
                + ", ".join(existing)
                + "; pakai --force untuk menimpa (config.yaml lama tetap dibackup)"
            )
    if dry_run:
        return files, dropped, None
    backup = base / f"config.yaml.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    backup.write_text((base / "config.yaml").read_text(encoding="utf-8"), encoding="utf-8")
    for name, content in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return files, dropped, backup


def report(files: dict[str, str], dropped: list[str], backup: Path | None) -> str:
    lines = ["config migrate:"]
    for name in files:
        lines.append(f"  tulis  {name}")
    if backup is not None:
        lines.append(f"  backup {backup}")
    lines.append("  dibuang: " + ", ".join(dropped))
    lines.append("Langkah berikut: sanka db upgrade, lalu sanka config import bila perlu.")
    return "\n".join(lines)
