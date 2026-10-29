"""Tool settings: baca/ubah engine/soul/persona/role lewat dokumen DB + mirror file."""

from __future__ import annotations

import difflib
import os
import re
import time
from dataclasses import fields, is_dataclass
from pathlib import Path

import yaml

from ..config import ConfigError, load_config
from ..db import redact_dsn
from ..registry import Handler
from ..roles import NAME as ROLE_NAME
from ..schema import config_changes
from . import ToolContext

MAX_CONTENT = 64 * 1024
SECRET = re.compile(r"(api_key|password|secret|token)", re.IGNORECASE)
FILES = ("engine", "host", "gateway", "soul", "persona", "role")
DOC_FILES = ("engine", "soul", "persona", "role")


# --------------------------------------------------------------- yaml util


def deep_get(body: object, path: str):
    node = body
    for part in [piece for piece in str(path or "").split(".") if piece]:
        if not isinstance(node, dict) or part not in node:
            raise KeyError(f"path tidak ada: {path}")
        node = node[part]
    return node


def deep_set(body: dict, path: str, value) -> None:
    parts = [piece for piece in str(path or "").split(".") if piece]
    if not parts:
        raise ValueError("path kosong")
    node = body
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            child = {}
            node[part] = child
        node = child
    node[parts[-1]] = value


def deep_unset(body: dict, path: str) -> bool:
    parts = [piece for piece in str(path or "").split(".") if piece]
    if not parts:
        raise ValueError("path kosong")
    node = body
    for part in parts[:-1]:
        child = node.get(part)
        if not isinstance(child, dict):
            return False
        node = child
    return node.pop(parts[-1], None) is not None


def dump(body: object) -> str:
    return yaml.safe_dump(body, sort_keys=False, allow_unicode=True)


def _diff(old: str, new: str, label: str) -> str:
    lines = difflib.unified_diff(
        old.splitlines(),
        new.splitlines(),
        fromfile=f"{label} (lama)",
        tofile=f"{label} (baru)",
        lineterm="",
        n=2,
    )
    text = "\n".join(lines)
    return text if len(text) <= 4000 else text[:3999] + "…"


def _schema_paths(prefix: str, body, depth: int = 0) -> list[str]:
    found: list[str] = []
    if not is_dataclass(body) or depth > 2:
        return found
    for item in fields(body):
        path = f"{prefix}.{item.name}" if prefix else item.name
        default = item.default if item.default is not item.default_factory else None
        found.append(f"{path} = {default!r}")
        value = getattr(body, item.name, None)
        if is_dataclass(value):
            found.extend(_schema_paths(path, value, depth + 1))
        elif isinstance(value, dict) and value and depth < 2:
            found.extend(_schema_paths(path, value, depth + 1))
    return found


# ------------------------------------------------------------------ state


def _mirror(agent, name: str) -> Path | None:
    paths = agent.config.paths
    if name == "engine":
        return paths.engine
    if name == "soul":
        return paths.soul
    if name == "persona":
        return paths.soul_md
    if name.startswith("role:"):
        return paths.roles / f"{name.split(':', 1)[1]}.yaml"
    return None


def _current(agent, file: str, role_name: str) -> tuple[str, str, Path | None]:
    """(kind, name, mirror) untuk file yang diminta."""
    if file == "role":
        if not ROLE_NAME.fullmatch(role_name or ""):
            raise ValueError("nama role tidak valid")
        name = f"role:{role_name}"
    elif file in DOC_FILES:
        name = file
    elif file in {"host", "gateway"}:
        path = agent.config.paths.config if file == "host" else agent.config.paths.gateway
        return "file", file, path
    else:
        raise ValueError(f"file harus salah satu: {', '.join(FILES)}")
    return "document", name, _mirror(agent, name)


def _read(agent, file: str, role_name: str) -> tuple[str, str, str]:
    kind, name, mirror = _current(agent, file, role_name)
    if kind == "document":
        text = agent.documents.get(name) if agent.documents is not None else None
        if text is None and mirror is not None and mirror.is_file():
            text = mirror.read_text(encoding="utf-8")
        return kind, name, text or ""
    path = mirror
    return kind, name, path.read_text(encoding="utf-8") if path and path.is_file() else ""


class CandidateDocuments:
    """Dokumen DB + satu kandidat, untuk validasi loader tanpa menulis."""

    def __init__(self, agent, name: str, content: str) -> None:
        self.agent = agent
        self.name = name
        self.content = content

    def get(self, name: str) -> str | None:
        if name == self.name:
            return self.content
        return self.agent.documents.get(name) if self.agent.documents is not None else None

    def names(self, prefix: str = "") -> list[str]:
        found = set(self.agent.documents.names(prefix)) if self.agent.documents else set()
        if self.name.startswith(prefix):
            found.add(self.name)
        return sorted(found)


def _validate(agent, file: str, name: str, text: str, role_name: str) -> None:
    if len(text.encode("utf-8")) > MAX_CONTENT:
        raise ValueError(f"konten melebihi {MAX_CONTENT} byte")
    if file in {"host", "gateway"}:
        body = yaml.safe_load(text)
        if not isinstance(body, dict):
            raise ValueError(f"{file} harus mapping YAML")
        if file == "gateway":
            gateways = body.get("gateways")
            if gateways is not None and not isinstance(gateways, dict):
                raise ValueError("gateway.gateways harus mapping")
            for gateway_name, value in (gateways or {}).items():
                if value is not None and not isinstance(value, dict):
                    raise ValueError(f"gateway '{gateway_name}' harus mapping")
                role = (value or {}).get("role") if isinstance(value, dict) else None
                if role is not None and agent.config.roles.get(str(role)) is None:
                    raise ValueError(f"gateway '{gateway_name}': role '{role}' tidak ada")
        else:
            if not isinstance(body.get("database"), dict) and not body.get("workspace"):
                raise ValueError("host config butuh database/workspace")
        return
    if file == "persona":
        return
    documents = CandidateDocuments(agent, name, text)
    loaded = load_config(
        agent.config.paths.config, documents=documents, require_key=False, require_gateway=False
    )
    if file == "role" and loaded.default_role not in loaded.roles.by_name:
        raise ValueError("default_role harus tetap ada")
    if file == "engine" and not loaded.roles.get(loaded.default_role):
        raise ValueError("default_role harus tetap ada")


def _audit(agent, actor: str, file: str, path: str, old: str, new: str) -> None:
    row = {
        "ts": time.time(),
        "actor": str(actor or ""),
        "file": file,
        "path": str(path or ""),
        "old": str(old)[:4000],
        "new": str(new)[:4000],
    }
    with agent.db.connect() as conn:
        conn.execute(config_changes.insert().values(row))


def _backup(path: Path) -> Path | None:
    if not path.is_file():
        return None
    target = path.with_name(f"{path.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    target.write_bytes(path.read_bytes())
    with __import__("contextlib").suppress(OSError):
        target.chmod(0o600)
    return target


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


async def _commit(agent, file: str, name: str, text: str, mirror: Path | None, actor: str) -> None:
    if file in {"host", "gateway"}:
        if mirror is not None:
            _backup(mirror)
            _atomic(mirror, text)
        return
    if agent.documents is None:
        raise ValueError("dokumen DB tidak tersedia")
    if mirror is not None:
        _backup(mirror)
    agent.documents.put(name, text, actor=actor)
    agent.documents.export_to_files()


# ------------------------------------------------------------------ actions


def _requires_approval(ctx: ToolContext, action: str) -> bool:
    if action in {"get", "schema", "reload"}:
        return False
    return bool(ctx.config.approval.settings) and not ctx.approved


def _payload(
    file: str, role_name: str, action: str, path: str, value, content: str, actor: str
) -> dict:
    return {
        "file": file,
        "role": role_name,
        "action": action,
        "path": path,
        "value": value,
        "content": content,
        "actor": actor,
    }


def _change(
    agent, file: str, role_name: str, action: str, path: str, value, content: str
) -> tuple[str, str, str, Path | None]:
    """Hitung isi baru tanpa menulis; kembalikan (kind, name, new_text, mirror)."""
    kind, name, current = _read(agent, file, role_name)
    mirror = _mirror(agent, name) if kind == "document" else (
        agent.config.paths.config if file == "host" else agent.config.paths.gateway
    )
    if action == "write":
        if not content:
            raise ValueError("content kosong")
        new_text = content
    else:
        if file == "persona":
            raise ValueError("persona hanya bisa diubah dengan action=write")
        body = yaml.safe_load(current) if current.strip() else {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise ValueError(f"{name} bukan mapping YAML")
        if action == "set":
            deep_set(body, path, value)
        elif action == "unset":
            if not deep_unset(body, path):
                raise KeyError(f"path tidak ada: {path}")
        else:
            raise ValueError("action harus get, schema, set, unset, write, atau reload")
        new_text = dump(body)
    if path and SECRET.search(path) and path != "api_key_env":
        raise ValueError("key sensitif tidak boleh diubah lewat settings")
    _validate(agent, file, name, new_text, role_name)
    return kind, name, new_text, mirror


async def _settings(args: dict, ctx: ToolContext) -> str:
    agent = ctx.agent
    if agent is None:
        return "error: settings hanya tersedia saat runtime agent"
    action = str(args.get("action") or "schema").strip().lower()
    file = str(args.get("file") or "").strip().lower()
    role_name = str(args.get("role") or "").strip()
    path = str(args.get("path") or "").strip()
    actor = ctx.sender or "unknown"

    if action == "schema":
        lines = [f"Files: {', '.join(FILES)}"]
        lines.append("Engine dot-paths:")
        lines.extend(f"- engine.{line}" for line in _schema_paths("engine", ctx.config))
        return "\n".join(lines)

    if action == "reload":
        return await agent.reload()

    if action == "get":
        kind, name, text = _read(agent, file, role_name)
        if file == "persona":
            return text or "(persona kosong)"
        body = yaml.safe_load(text) if text.strip() else {}
        if path:
            try:
                value = deep_get(body, path)
            except KeyError as exc:
                return f"error: {exc}"
            if isinstance(value, (dict, list)):
                return dump(value).strip()
            return str(value)
        return text or "(kosong)"

    if action not in {"set", "unset", "write"}:
        return "error: action harus schema, get, set, unset, write, atau reload"
    try:
        kind, name, new_text, mirror = _change(
            agent, file, role_name, action, path, args.get("value"), str(args.get("content") or "")
        )
    except (ValueError, KeyError, ConfigError) as exc:
        return f"error: {exc}"
    _, _, old_text = _read(agent, file, role_name)
    diff = _diff(old_text, new_text, name)
    display = new_text
    if path == "database.url" and new_text:
        display = redact_dsn(new_text)
    if _requires_approval(ctx, action):
        approval = await agent.approvals.create(
            kind="settings",
            chat_id=ctx.chat_id,
            sender=ctx.sender,
            gateway=getattr(ctx.gateway, "name", "") or "",
            tool="settings",
            payload=_payload(
                file, role_name, action, path, args.get("value"), str(args.get("content") or ""), actor
            ),
            summary=f"settings {action} {name}{(' ' + path) if path else ''}",
            diff=diff,
            roles=tuple(role.name for role in ctx.roles),
        )
        return (
            f"pending approval #{approval.id}: settings {action} {name}. "
            f"Diff:\n{diff}\nTell the sender to run /approve {approval.id}."
        )
    await _commit(agent, file, name, new_text, mirror, actor)
    _audit(agent, actor, file, path or name, old_text, display)
    try:
        summary = await agent.reload()
    except ConfigError as exc:
        return f"ok: disimpan; {exc}"
    return f"ok: {action} {name} disimpan. {summary}"


async def apply_payload(agent, payload: dict) -> str:
    """Dipanggil /approve: validasi ulang lalu tulis + mirror + audit + reload."""
    file = str(payload.get("file") or "")
    action = str(payload.get("action") or "")
    role_name = str(payload.get("role") or "")
    path = str(payload.get("path") or "")
    actor = str(payload.get("actor") or "approval")
    kind, name, new_text, mirror = _change(
        agent, file, role_name, action, path, payload.get("value"), str(payload.get("content") or "")
    )
    _, _, old_text = _read(agent, file, role_name)
    await _commit(agent, file, name, new_text, mirror, actor)
    display = redact_dsn(new_text) if path == "database.url" else new_text
    _audit(agent, actor, file, path or name, old_text, display)
    try:
        summary = await agent.reload()
    except ConfigError as exc:
        return f"disimpan; {exc}"
    return summary


TOOL = Handler(
    name="settings",
    family="write",
    description=(
        "Read or change Sanka's own config/soul/persona/roles. Actions: schema, get, set, unset, "
        "write, reload. Files: engine, host, gateway, soul, persona, role:<name>. Engine/soul/"
        "persona/role live in the shared DB with file mirrors; host/gateway are host-local files. "
        "Sensitive changes wait for /approve when approval.settings is true."
    ),
    fn=_settings,
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "schema, get, set, unset, write, reload."},
            "file": {"type": "string", "description": "engine, host, gateway, soul, persona, role."},
            "role": {"type": "string", "description": "Role name when file=role."},
            "path": {"type": "string", "description": "Dot-path inside the file."},
            "value": {"description": "New value for set (YAML/JSON)."},
            "content": {"type": "string", "description": "Full content for writing persona/role."},
        },
        "additionalProperties": False,
    },
)
