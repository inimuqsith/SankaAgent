# SankaAgent

Bot WhatsApp pribadi: satu proses Python (Neonize) + model OpenAI-compatible + tool.
DM selalu dilayani; grup hanya saat bot di-mention atau dibalas. Lampiran diabaikan.

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

`tools:` dan `commands:` boleh dikosongkan: pola default tiap handler yang dipakai (lihat di bawah).
Kalau ditulis, isi YAML menang dan default diabaikan.

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

Tool baru = entri `Handler(...)` di `HANDLERS` (`sanka/tools.py`) dengan `parameters` skema JSON.
`defaults` memetakan nama role ke bool; role tanpa daftar `tools:`/`commands:` memakai default itu.

## Jalankan

```bash
python -m sanka               # pairing: scan QR di terminal
python -m sanka selftest      # cek tool + LLM tanpa WhatsApp
```

Session WhatsApp disimpan di `whatsapp.session_db`, jadi QR hanya muncul saat pairing pertama.
`shell` dan `sandbox` memberi akses host/container penuh: beri hanya ke role yang dipercaya.
