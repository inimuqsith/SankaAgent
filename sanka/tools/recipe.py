"""Tool recipe: satu pintu agent untuk mendaftarkan/mengedit/menghapus resep user."""

from __future__ import annotations

from ..registry import Handler
from . import OWNER_MEMBER, ToolContext


def _view(recipe) -> str:
    lines = [
        f'Resep "/{recipe.name}": {recipe.description}',
        f"kind: {recipe.kind}",
        f"usage: {recipe.usage or '-'}",
        f"roles: {', '.join(recipe.roles)}",
    ]
    if recipe.kind == "recipe":
        for i, step in enumerate(recipe.steps, 1):
            lines.append(f"langkah {i}: {step['tool']} {step['args']}")
    else:
        lines.append(f"prompt: {recipe.prompt}")
    return "\n".join(lines)


async def _recipe(args: dict, ctx: ToolContext) -> str:
    if ctx.recipes is None:
        return "error: resep tidak tersedia"
    action = str(args.get("action") or "list").strip().lower()
    name = str(args.get("name") or "").strip()

    if action == "list":
        return ctx.recipes.index() or "Belum ada resep user."

    if action == "view":
        if not name:
            return "error: name kosong"
        recipe = ctx.recipes.get(name)
        if recipe is None:
            return f"error: resep tidak ditemukan: {name}"
        return _view(recipe)

    if action == "save":
        try:
            ctx.recipes.save(
                name,
                str(args.get("kind") or ""),
                str(args.get("description") or ""),
                usage=str(args.get("usage") or ""),
                roles=args.get("roles") or ("owner", "member"),
                steps=args.get("steps") or (),
                prompt=str(args.get("prompt") or ""),
                overwrite=bool(args.get("overwrite", False)),
            )
        except (ValueError, FileExistsError, OSError) as exc:
            return f"error: {exc}"
        kind = str(args.get("kind") or "").strip()
        detail = (
            f"{len(args.get('steps') or ())} langkah" if kind == "recipe" else "prompt"
        )
        return f"ok: resep /{name} disimpan ({detail})"

    if action == "remove":
        if not name:
            return "error: name kosong"
        try:
            ctx.recipes.remove(name)
        except (KeyError, OSError) as exc:
            return f"error: {exc}"
        return f"ok: resep /{name} dihapus"

    return "error: action harus list, view, save, atau remove"


TOOL = Handler(
    name="recipe",
    family="read",
    description=(
        "Resep user: list/view untuk melihat command dinamis, save untuk mendaftarkan "
        "(kind=recipe berisi langkah tool dengan {{args}}, kind=prompt berisi instruksi "
        "satu turn LLM), remove untuk menghapus. Daftarkan hanya bila user meminta "
        "otomatisasi berulang; kutip {{args}} dalam command shell."
    ),
    fn=_recipe,
    parameters={
        "type": "object",
        "properties": {
            "action": {"type": "string", "description": "list, view, save, atau remove."},
            "name": {"type": "string", "description": "Nama resep (lowercase, hyphen)."},
            "kind": {"type": "string", "description": "recipe atau prompt."},
            "description": {"type": "string", "description": "Deskripsi singkat (tampil di /help)."},
            "usage": {"type": "string", "description": "Petunjuk argumen, mis. [divisi]."},
            "roles": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Role yang boleh mengeksekusi; default owner+member.",
            },
            "steps": {
                "type": "array",
                "items": {"type": "object"},
                "description": "Langkah tool [{tool, args}] untuk kind=recipe.",
            },
            "prompt": {"type": "string", "description": "Instruksi satu turn untuk kind=prompt."},
            "overwrite": {"type": "boolean", "description": "Timpa resep yang sudah ada saat save."},
        },
        "additionalProperties": False,
    },
    defaults=OWNER_MEMBER,
)
