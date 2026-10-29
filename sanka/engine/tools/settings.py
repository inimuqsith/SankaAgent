"""Settings tool: read/change engine/soul/persona/role documents and host config files."""

from __future__ import annotations

import contextlib
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
RESERVED_SECTIONS = ("engine", "gateways", "soul")
MIRRORS = ("config.yaml", "roles.yaml", "SOUL.md")  # satu-satunya berkas yang di-mirror


# --------------------------------------------------------------- yaml util


def deep_get(body: object, path: str):
    node = body
    for piece in [part for part in str(path or "").split(".") if part]:
        if not isinstance(node, dict) or piece not in node:
            raise KeyError(f"path not found: {path}")
        node = node[piece]
    return node


def deep_set(body: dict, path: str, value) -> None:
    parts = [piece for piece in str(path or "").split(".") if piece]
    if not parts:
        raise ValueError("path is required")
    node = body
    for piece in parts[:-1]:
        child = node.get(piece)
        if not isinstance(child, dict):
            child = {}
            node[piece] = child
        node = child
    node[parts[-1]] = value


def deep_unset(body: dict, path: str) -> bool:
    parts = [piece for piece in str(path or "").split(".") if piece]
    if not parts:
        raise ValueError("path is required")
    node = body
    for piece in parts[:-1]:
        child = node.get(piece)
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
        fromfile=f"{label} (current)",
        tofile=f"{label} (new)",
        lineterm="",
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


def _read_config(agent) -> dict:
    path = agent.config.paths.config
    if not path.is_file():
        return {}
    try:
        body = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"config.yaml is invalid: {exc}") from exc
    return body if isinstance(body, dict) else {}


def _write_config(agent, body: dict) -> None:
    path = agent.config.paths.config
    _backup(path)
    _atomic(path, dump(body))


def _read_roles(agent) -> dict:
    path = agent.config.paths.roles
    if not path.is_file():
        return {}
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as exc:
        raise ConfigError(f"roles.yaml is invalid: {exc}") from exc
    body = raw.get("roles") if isinstance(raw, dict) else None
    return body if isinstance(body, dict) else {}


def _write_roles(agent, roles: dict) -> None:
    path = agent.config.paths.roles
    _backup(path)
    _atomic(path, dump({"roles": roles}))
    with contextlib.suppress(OSError):
        path.chmod(0o600)


def _mirror(agent, file: str) -> Path | None:
    paths = agent.config.paths
    if file in {"engine", "soul", "gateway"}:
        return paths.config
    if file == "persona":
        return paths.soul_md
    if file == "role":
        return paths.roles
    return None


def _current(agent, file: str, role_name: str) -> tuple[str, str]:
    """(kind, name) for the requested file; kind is host, gateway, or document."""
    if file == "role":
        if not ROLE_NAME.fullmatch(role_name or ""):
            raise ValueError("invalid role name")
        return "document", f"role:{role_name}"
    if file in DOC_FILES:
        return "document", file
    if file == "host":
        return "host", "host"
    if file == "gateway":
        return "gateway", "gateway"
    raise ValueError(f"file must be one of: {', '.join(FILES)}")


def _read(agent, file: str, role_name: str) -> tuple[str, str, str]:
    kind, name = _current(agent, file, role_name)
    if kind == "host":
        text = agent.config.paths.config.read_text(encoding="utf-8")
        return kind, name, text
    if kind == "gateway":
        raw = _read_config(agent)
        return kind, name, dump({"gateways": raw.get("gateways") or {}})
    text = agent.documents.get(name) if agent.documents is not None else None
    if text is not None:
        return kind, name, text
    if name in {"engine", "soul"}:
        raw = _read_config(agent)
        body = raw.get(name)
        return kind, name, dump(body) if isinstance(body, dict) else ""
    if name == "persona":
        path = agent.config.paths.soul_md
        return kind, name, path.read_text(encoding="utf-8") if path.is_file() else ""
    roles = _read_roles(agent)
    body = roles.get(name.split(":", 1)[1]) if ":" in name else None
    return kind, name, dump(body) if isinstance(body, dict) else ""


class CandidateDocuments:
    """DB documents plus one candidate, for validating through the loader without writing."""

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


def _validate(agent, file: str, name: str, text: str) -> None:
    if len(text.encode("utf-8")) > MAX_CONTENT:
        raise ValueError(f"content exceeds {MAX_CONTENT} bytes")
    if file == "persona":
        return
    if file in {"host", "gateway"}:
        body = yaml.safe_load(text)
        if not isinstance(body, dict):
            raise ValueError(f"{file} must be a YAML mapping")
        if file == "gateway":
            gateways = body.get("gateways")
            if gateways is not None and not isinstance(gateways, dict):
                raise ValueError("gateway.gateways must be a mapping")
            for gateway_name, value in (gateways or {}).items():
                if value is not None and not isinstance(value, dict):
                    raise ValueError(f"gateway '{gateway_name}' must be a mapping")
                role = (value or {}).get("role") if isinstance(value, dict) else None
                if role is not None and agent.config.roles.get(str(role)) is None:
                    raise ValueError(f"gateway '{gateway_name}': role '{role}' does not exist")
        else:
            if not isinstance(body.get("database"), dict) and not body.get("workspace"):
                raise ValueError("host config needs database/workspace")
            for key in RESERVED_SECTIONS:
                if key in body and not isinstance(body.get(key), dict):
                    raise ValueError(f"host section '{key}' must stay a mapping")
        return
    documents = CandidateDocuments(agent, name, text)
    loaded = load_config(
        agent.config.paths.config, documents=documents, require_key=False, require_gateway=False
    )
    if file == "role" and loaded.default_role not in loaded.roles.by_name:
        raise ValueError("default_role must still exist")
    if file == "engine" and not loaded.roles.get(loaded.default_role):
        raise ValueError("default_role must still exist")


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
    resolved = path.resolve()
    if resolved.name not in MIRRORS or not resolved.is_file():
        return None
    target = resolved.with_name(f"{resolved.name}.bak-{time.strftime('%Y%m%d-%H%M%S')}")
    target.write_bytes(resolved.read_bytes())
    with contextlib.suppress(OSError):
        target.chmod(0o600)
    return target


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


async def _commit(agent, file: str, name: str, text: str, actor: str) -> None:
    if file == "host":
        _write_config(agent, yaml.safe_load(text) or {})
        return
    if file == "gateway":
        raw = _read_config(agent)
        body = yaml.safe_load(text) or {}
        raw["gateways"] = body.get("gateways") or {}
        _write_config(agent, raw)
        return
    if agent.documents is None:
        raise ValueError("DB documents are not available")
    mirror = _mirror(agent, file)
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
) -> tuple[str, str, str]:
    """Compute the new content without writing; return (kind, name, new_text)."""
    kind, name, current = _read(agent, file, role_name)
    if action == "write":
        if not content:
            raise ValueError("content is empty")
        new_text = content
    else:
        if file == "persona":
            raise ValueError("persona can only change with action=write")
        if file == "gateway" and path.startswith("gateways."):
            raise ValueError("gateway paths are relative to the gateways mapping")
        if file == "host":
            head = str(path or "").split(".", 1)[0]
            if head in RESERVED_SECTIONS:
                raise ValueError("use file=engine, file=gateway, or file=soul")
        body = yaml.safe_load(current) if current.strip() else {}
        if body is None:
            body = {}
        if not isinstance(body, dict):
            raise ValueError(f"{name} is not a YAML mapping")
        if file == "gateway":
            gateways = body.get("gateways")
            body = gateways if isinstance(gateways, dict) else {}
        if action == "set":
            deep_set(body, path, value)
        elif action == "unset":
            if not deep_unset(body, path):
                raise KeyError(f"path does not exist: {path}")
        else:
            raise ValueError("action must be get, schema, set, unset, write, or reload")
        new_text = dump({"gateways": body}) if file == "gateway" else dump(body)
    if path and SECRET.search(path) and path != "api_key_env":
        raise ValueError("sensitive keys cannot be changed through settings")
    _validate(agent, file, name, new_text)
    return kind, name, new_text


async def _settings(args: dict, ctx: ToolContext) -> str:
    agent = ctx.agent
    if agent is None:
        return "error: settings is only available at agent runtime"
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
            return text or "(persona is empty)"
        body = yaml.safe_load(text) if text.strip() else {}
        if path:
            try:
                value = deep_get(body, path)
            except KeyError as exc:
                return f"error: {exc}"
            if isinstance(value, (dict, list)):
                return dump(value).strip()
            return str(value)
        return text or "(empty)"

    if action not in {"set", "unset", "write"}:
        return "error: action must be schema, get, set, unset, write, or reload"
    try:
        kind, name, new_text = _change(
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
    await _commit(agent, file, name, new_text, actor)
    _audit(agent, actor, file, path or name, old_text, display)
    try:
        summary = await agent.reload()
    except ConfigError as exc:
        return f"ok: saved; {exc}"
    return f"ok: {action} {name} saved. {summary}"


async def apply_payload(agent, payload: dict) -> str:
    """Called by /approve: validate again, then write, mirror, audit, and reload."""
    file = str(payload.get("file") or "")
    action = str(payload.get("action") or "")
    role_name = str(payload.get("role") or "")
    path = str(payload.get("path") or "")
    actor = str(payload.get("actor") or "approval")
    kind, name, new_text = _change(
        agent, file, role_name, action, path, payload.get("value"), str(payload.get("content") or "")
    )
    _, _, old_text = _read(agent, file, role_name)
    await _commit(agent, file, name, new_text, actor)
    display = redact_dsn(new_text) if path == "database.url" else new_text
    _audit(agent, actor, file, path or name, old_text, display)
    try:
        summary = await agent.reload()
    except ConfigError as exc:
        return f"saved; {exc}"
    return summary


TOOL = Handler(
    name="settings",
    family="write",
    description=(
        "Read or change Sanka's own config/soul/persona/roles. Actions: schema, get, set, unset, "
        "write, reload. Files: engine, host, gateway, soul, persona, role:<name>. Engine/soul "
        "live in the `engine`/`soul` sections of config.yaml and in DB documents; roles live in "
        "roles.yaml and DB `role:<name>` documents; host is the whole config.yaml. Sensitive "
        "changes wait for /approve when approval.settings is true."
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
