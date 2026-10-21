"""Tool skill: satu pintu untuk memuat, menyimpan, dan mengarsipkan skill lokal."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext


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
            path = ctx.skills.archive(name)
        except (KeyError, ValueError, OSError) as exc:
            return f"error: {exc}"
        return f"ok: skill diarsipkan ke {path}"

    return "error: action harus list, view, read, save, atau archive"


TOOL = Handler(
    name="skill",
    family="read",
    description=(
        "Skill lokal: list/view/read untuk memakai prosedur, save untuk menyimpan/memperbarui, "
        "archive untuk memensiunkan. AI memutuskan kapan skill layak dipakai/disimpan."
    ),
    fn=_skill,
    aliases=("skills", "sop"),
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list, view, read, save, atau archive."},
            "name": {"type": "string", "description": "Nama skill (lowercase, hyphen)."},
            "file": {"type": "string", "description": "Path file pendukung relatif ke folder skill."},
            "description": {"type": "string", "description": "Deskripsi singkat saat save."},
            "body": {"type": "string", "description": "Isi prosedur markdown saat save."},
            "overwrite": {"type": "boolean", "description": "Timpa skill yang sudah ada saat save."},
        },
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
