# SankaAgent

Bot WhatsApp pribadi: satu proses Python (Neonize) + model OpenAI-compatible + tool.
DM selalu dilayani; grup hanya saat bot di-mention atau dibalas. Lampiran diabaikan.
Pesan yang dilayani langsung ditandai "mengetik" sampai balasan terkirim (disentuh ulang tiap 8 detik).

## Pasang

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
cp .env.example .env            # isi OPENCODE_GO_API_KEY
cp config.example.yaml config.yaml
```

Prasyarat: Python 3.11+, ffmpeg (Neonize), `libmagic`. Podman dipakai tool `sandbox`.

## Konfigurasi

`config.yaml` (gitignored) memuat semua perilaku. Nomor owner ditulis internasional tanpa `+`.

| Bagian | Isi |
| --- | --- |
| `persona` | system prompt dasar |
| `llm` | `base_url`, `model`, `api_key_env`, `max_turns`, `timeout_seconds` |
| `workspace` | direktori kerja tool `read`/`write`/`shell`/`sandbox` |
| `whatsapp` | `session_db`, `require_mention`, `bot_name` |
| `roles` | `users` (nomor), `tools`, `commands`; pola glob (`*`, `mcp__*`) |
| `default_role` | role untuk nomor tak dikenal |
| `sandbox` | `runtime` (podman), `image`, `timeout_seconds` |
| `mcp.servers` | `command`, `args`, `env` per server stdio |

`tools:` dan `commands:` yang tidak ditulis memakai pola default tiap handler (lihat di bawah);
kalau ditulis, isi YAML menang. `tools: []` atau `commands: []` berarti role itu tidak boleh apa pun.

Tool yang tersedia: `shell`, `read`, `write`, `web_search`, `sandbox`, `mcp__<server>__<tool>`.
Command: `/ping`, `/help`, `/reset` (plus alias `/p`, `/h`, `/r`). `/help` membaca metadata registry.

Kalau WhatsApp mengalamatkan pengirim lewat LID (`...@lid`), cocokkan role dengan digit LID itu,
bukan nomor telepon: tambahkan keduanya di `roles.<nama>.users` bila perlu.

## Menambah command atau tool

Command baru = satu modul di `sanka/commands/`, registry meng-import otomatis:

```python
from ..registry import Handler
from . import CommandContext

async def echo(ctx: CommandContext) -> str:
    return ctx.args

COMMAND = Handler(
    name="echo", description="Ulangi argumen.", fn=echo,
    aliases=("e",), usage="<teks>", defaults={"owner": True, "member": True},
)
```

Tool baru = satu modul di `sanka/tools/`, registry meng-import otomatis dengan kontrak yang sama;
`family` wajib salah satu dari `FAMILIES` dan nama file harus sama dengan `name`:

```python
from ..registry import Handler
from . import OWNER_MEMBER, ToolContext, _resolve

async def _stat(args: dict, ctx: ToolContext) -> str:
    path = _resolve(ctx, args.get("path", ""))
    return f"{path}: {path.stat().st_size} byte" if path.is_file() else "error: file tidak ditemukan"

TOOL = Handler(
    name="stat", family="read", description="Ukuran file di workspace.", fn=_stat,
    parameters={"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]},
    defaults=OWNER_MEMBER,
)
```

Handler tidak valid (nama, alias, `family`, skema `parameters`, atau `defaults` salah) menggagalkan
start dengan satu pesan berisi semua modul bermasalah. Modul helper diberi awalan `_`.
`defaults` memetakan nama role ke bool; role tanpa daftar `tools:`/`commands:` memakai default itu,
sedangkan `tools: []` / `commands: []` berarti tolak semua.

## Jalankan

```bash
python -m sanka               # pairing: scan QR di terminal
python -m sanka selftest      # cek tool + LLM tanpa WhatsApp
```

Session WhatsApp disimpan di `whatsapp.session_db`, jadi QR hanya muncul saat pairing pertama.
`shell` dan `sandbox` memberi akses host/container penuh: beri hanya ke role yang dipercaya.
