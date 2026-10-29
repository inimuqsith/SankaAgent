"""Tool skill: satu pintu untuk memuat, menyimpan, dan mengarsipkan skill lokal."""

from __future__ import annotations

from ..registry import Handler
from . import ToolContext


async def _skill(args: dict, ctx: ToolContext) -> str:
    if ctx.skills is None:
        return "error: skill tidak tersedia"
    action = str(args.get("action") or "list").strip().lower()
    name = str(args.get("name") or "").strip()

    if action == "list":
        return ctx.skills.index() or "Belum ada skill lokal."

    if action in {"view", "show", "load"}:
        if not name:
            return "error: name kosong"
        try:
            return ctx.skills.view(name)
        except KeyError as exc:
            return f"error: {exc}"

    if action == "read":
        if not name:
            return "error: name kosong"
        try:
            return ctx.skills.read(name, str(args.get("file") or ""))
        except (KeyError, ValueError, OSError) as exc:
            return f"error: {exc}"

    if action == "save":
        try:
            path = ctx.skills.save(
                name,
                str(args.get("description") or ""),
                str(args.get("body") or ""),
                overwrite=bool(args.get("overwrite", False)),
            )
        except (ValueError, OSError) as exc:
            return f"error: {exc}"
        return f"ok: skill disimpan di {path}"

    if action == "archive":
        try:
            saved = ctx.skills.archive(name)
        except (KeyError, ValueError, OSError) as exc:
            return f"error: {exc}"
        return f"ok: skill {saved} diarsipkan"

    return "error: action harus list, view, read, save, atau archive"


TOOL = Handler(
    name="skill",
    family="read",
    description=(
        "Local skills: list/view/read to use a procedure, save to store or update one, "
        "archive to retire it. You decide when a skill is worth using or saving."
    ),
    fn=_skill,
    aliases=("skills", "sop"),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list, view, read, save, or archive."},
            "name": {"type": "string", "description": "Skill name (lowercase, hyphen)."},
            "file": {"type": "string", "description": "Supporting file path relative to the skill folder."},
            "description": {"type": "string", "description": "Short description when saving."},
            "body": {"type": "string", "description": "Markdown procedure body when saving."},
            "overwrite": {"type": "boolean", "description": "Overwrite an existing skill when saving."},
        },
        "additionalProperties": False,
    },
)
