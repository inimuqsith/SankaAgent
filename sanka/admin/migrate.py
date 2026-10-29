"""`sanka config migrate`: fold the old split layout into config.yaml + roles.yaml."""

from __future__ import annotations

import contextlib
import shutil
import time
from pathlib import Path

import yaml

from ..engine.config import ConfigError
from ..engine.soul import infer_language
from . import templates

# Snapshot of the old handler defaults (Handler.defaults) so role files stay explicit.
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
SPLIT_KEYS = ("engine_file", "gateway_file", "roles_dir", "soul_file")
OLD_FILES = ("engine.yaml", "gateway.yaml", "soul.yaml")
NEW_FILES = ("config.yaml", "roles.yaml")
ALREADY = "config.yaml is already combined; nothing to migrate"
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
    "engine_file",
    "gateway_file",
    "roles_dir",
    "soul_file",
)


def _section(raw: dict, key: str) -> dict:
    body = raw.get(key)
    return dict(body) if isinstance(body, dict) else {}


def _yaml_file(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"{path.name} is invalid: {exc}") from exc
    return body if isinstance(body, dict) else {}


def _host(raw: dict) -> dict:
    host = dict(templates.HOST)
    host["workspace"] = str(raw.get("workspace") or host["workspace"])
    if isinstance(raw.get("database"), dict):
        database = dict(templates.HOST["database"])
        database.update({k: v for k, v in raw["database"].items() if k in database})
        host["database"] = database
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
    if "sync_documents" in raw:
        host["sync_documents"] = raw["sync_documents"]
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


def _gateways(raw: dict, default_role: str) -> dict:
    wa = _section(raw, "whatsapp")
    return {
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


def _persona(base: Path) -> str:
    path = base / "SOUL.md"
    if not path.is_file():
        return ""
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _legacy_soul(raw: dict, base: Path) -> dict:
    wa = _section(raw, "whatsapp")
    return {
        "name": str(wa.get("bot_name") or "sanka"),
        "language": infer_language(_persona(base)),
        "pronouns": "aku",
    }


def _legacy_roles(raw: dict) -> dict[str, dict]:
    old = raw.get("roles")
    if not isinstance(old, dict) or not old:
        raise ConfigError("legacy config has no 'roles' to migrate")
    roles: dict[str, dict] = {}
    for name, body in old.items():
        body = body if isinstance(body, dict) else {}
        defaults = LEGACY_DEFAULTS.get(str(name), {"tools": [], "commands": []})
        tools = body.get("tools")
        commands = body.get("commands")
        roles[str(name)] = {
            "name": str(name),
            "description": str(body.get("description") or f"Role {name}"),
            "priority": int(PRIORITY.get(str(name), 1)),
            "users": list(body.get("users") or []),
            "tools": list(defaults["tools"] if tools is None else tools),
            "commands": list(defaults["commands"] if commands is None else commands),
            "memory_admin": "*" in (tools if tools is not None else defaults["tools"]),
            "context": str(body.get("context") or ""),
        }
    return roles


def _split_files(base: Path, raw: dict) -> tuple[dict, dict, dict, dict[str, dict]]:
    """Read the current split layout: engine/gateway/soul YAML plus roles/*.yaml."""
    engine_path = base / str(raw.get("engine_file") or "engine.yaml")
    gateway_path = base / str(raw.get("gateway_file") or "gateway.yaml")
    soul_path = base / str(raw.get("soul_file") or "soul.yaml")
    roles_dir = base / str(raw.get("roles_dir") or "roles")
    engine = _yaml_file(engine_path)
    gateways = (_yaml_file(gateway_path) or {}).get("gateways")
    gateways = gateways if isinstance(gateways, dict) else {}
    soul = _yaml_file(soul_path)
    roles: dict[str, dict] = {}
    if roles_dir.is_dir():
        for path in sorted(roles_dir.glob("*.yaml")):
            body = _yaml_file(path)
            if body:
                body.setdefault("memory_admin", "*" in (body.get("tools") or []))
                roles[path.stem] = body
    return engine, gateways, soul, roles


def _combined(host: dict, engine: dict, gateways: dict, soul: dict) -> str:
    body = {**host, "engine": engine, "gateways": gateways, "soul": soul}
    return templates.dump(body)


def plan(folder: str | Path) -> tuple[dict[str, str], list[str]]:
    """Compute the new files without writing; return (files, notes about dropped keys)."""
    base = Path(folder).expanduser().resolve()
    target = base / "config.yaml"
    if not target.is_file():
        raise ConfigError(f"config.yaml not found: {target}")
    try:
        raw = yaml.safe_load(target.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config.yaml is invalid: {exc}") from exc
    if not isinstance(raw, dict):
        raise ConfigError("config.yaml is empty or not a mapping")

    split = any(key in raw for key in SPLIT_KEYS)
    sections = {"engine", "gateways", "soul"}
    if not split and not sections & set(raw) and "whatsapp" not in raw and "roles" not in raw:
        raise ConfigError("config.yaml is not a legacy or split layout")
    if not split and "whatsapp" not in raw and "roles" not in raw and sections & set(raw):
        return {"config.yaml": target.read_text(encoding="utf-8")}, [ALREADY]

    if split:
        engine, gateways, soul, roles = _split_files(base, raw)
        if not engine:
            engine = dict(templates.ENGINE)
        if not gateways:
            gateways = dict(templates.GATEWAY["gateways"])
        if not soul:
            soul = {
                "name": "sanka",
                "language": infer_language(_persona(base)),
                "pronouns": "aku",
            }
        host = _host(raw)
    else:
        default_role = str(raw.get("default_role") or "")
        old_roles = raw.get("roles") if isinstance(raw.get("roles"), dict) else {}
        if default_role not in old_roles:
            raise ConfigError(f"default_role '{default_role}' is not among the legacy roles")
        engine = _engine(raw, default_role)
        gateways = _gateways(raw, default_role)
        soul = _legacy_soul(raw, base)
        roles = _legacy_roles(raw)
        host = _host(raw)

    return (
        {
            "config.yaml": _combined(host, engine, gateways, soul),
            "roles.yaml": templates.dump({"roles": roles}),
        },
        list(DROPPED),
    )


def _backup(base: Path) -> Path:
    """Copy every old config artifact into config.bak-<timestamp>/ before writing."""
    folder = base / f"config.bak-{time.strftime('%Y%m%d-%H%M%S')}"
    folder.mkdir(parents=True, exist_ok=True)
    for name in (*NEW_FILES, *OLD_FILES):
        source = base / name
        if source.is_file():
            shutil.copy2(source, folder / name)
    roles = base / "roles"
    if roles.is_dir():
        shutil.copytree(roles, folder / "roles", dirs_exist_ok=True)
    return folder


def migrate(
    folder: str | Path,
    *,
    dry_run: bool = False,
    force: bool = False,
) -> tuple[dict[str, str], list[str], Path | None]:
    """Write the migrated files; back up the old ones. Return (files, dropped, backup)."""
    base = Path(folder).expanduser().resolve()
    files, dropped = plan(base)
    if ALREADY in dropped:
        return files, dropped, None
    if not force and (base / "roles.yaml").exists():
        raise ConfigError(
            "roles.yaml already exists; use --force to overwrite (the old files stay backed up)"
        )
    if dry_run:
        return files, dropped, None
    backup = _backup(base)
    for name, content in files.items():
        path = base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        if name == "roles.yaml":
            with contextlib.suppress(OSError):
                path.chmod(0o600)
    if force:
        for name in OLD_FILES:
            (base / name).unlink(missing_ok=True)
        shutil.rmtree(base / "roles", ignore_errors=True)
    return files, dropped, backup


def report(files: dict[str, str], dropped: list[str], backup: Path | None) -> str:
    lines = ["config migrate:"]
    if ALREADY in dropped:
        lines.append("  unchanged: config.yaml is already combined")
        return "\n".join(lines)
    for name in files:
        lines.append(f"  wrote  {name}")
    if backup is not None:
        lines.append(f"  backup {backup}")
    lines.append("  dropped: " + ", ".join(dropped))
    lines.append("Next: sanka db upgrade, then sanka config import if needed.")
    return "\n".join(lines)
