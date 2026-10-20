# Plan 11 — Konteks ambient chat grup

- Status: Done
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Sanka selama ini hanya "mendengar" pesan grup yang di-mention/di-reply. Owner ingin Sanka memahami konteks seluruh obrolan grup, bukan hanya pesan yang ditujukan kepadanya.

Plan ini menambahkan buffer konteks grup:

- Semua pesan teks grup yang lolos gate grup dicatat (termasuk yang tidak di-mention).
- Disimpan sebagai JSONL di luar repo, dengan hash `chat_id`, permission ketat, retensi dan batas ukuran.
- Saat Sanka menjawab di grup, potongan konteks terbaru dimasukkan ke system prompt sebagai blok `<group_context>` yang eksplisit **DATA, bukan instruksi**.
- Nama pengirim ikut dicatat agar model tahu siapa bicara.
- DM tidak pernah dicatat.

## Di luar cakupan

- Transkripsi voice note, OCR gambar, atau isi media. Hanya teks/kaption.
- Menyimpan balasan Sanka sendiri ke konteks grup; history sesi sudah memuat balasan pada giliran yang di-mention.
- Memori jangka panjang lintas hari (plan-7). Ini buffer konteks operasional, bukan memori.
- Perintah untuk melihat/menghapus konteks dari WhatsApp. Penghapusan lewat filesystem oleh owner.
- Ringkasan otomatis oleh LLM. Konteks dipotong deterministik, bukan diringkas.
- Perubahan pada bridge selain menambahkan `sender_name` (push name).

## Rancangan

### Alur data

```text
Baileys (semua pesan grup) -> bridge /v1/inbound (sender_name)
  -> normalizer -> Dispatcher
       gate grup lolos?
         ya  -> GroupContextStore.record() untuk pesan yang tidak diproses agent
                atau setelah giliran agent selesai (agar pesan saat ini tidak dobel)
         tidak -> buang
  -> AgentRuntime (khusus mention/reply)
       baca GroupContextStore.context(chat_id)
       -> build_system_prompt(group_context=...) -> LLM
```

Bridge sudah meneruskan semua pesan grup (bukan hanya mention); tidak perlu perubahan routing di Node selain `sender_name`.

### `sanka/group_context.py`

```python
@dataclass(frozen=True, slots=True)
class GroupMessage:
    timestamp: float
    message_id: str
    sender_id: str
    sender_name: str
    text: str


class GroupContextStore:
    def __init__(self, root: Path, *, retention_seconds, max_messages,
                 inject_messages, inject_max_chars, capture_all,
                 capture_group_ids, clock=time.time) -> None: ...
    def captures(self, chat_id: str) -> bool: ...
    def record(self, message: InboundMessage, timestamp: float) -> None: ...
    def context(self, chat_id: str) -> str | None: ...
```

Aturan penyimpanan:

- Hanya grup (`message.is_group`), teks tidak kosong, bukan command (`/...`).
- Teks dinormalisasi menjadi satu baris, maksimal 1.000 karakter per entri.
- `sender_name` dari `pushName`, dibersihkan dari karakter kontrol/newline, maksimal 40 karakter; fallback `Peserta <4 digit akhir>`.
- Satu file JSONL per grup: `HMAC-SHA256(salt, chat_id)[:32].jsonl`. `chat_id` mentah tidak muncul di nama file maupun isi.
- Direktori `0700`, file `0600`, salt `0600`. Root wajib absolut dan di luar repo.
- Baris: `{"ts": <epoch>, "id": ..., "sender": ..., "name": ..., "text": ...}`.
- Retensi `retention_hours`, maksimal `max_messages`; file ditulis ulang atomik saat melewati 2x batas.
- Saat proses restart, file dibaca ulang (tail), sehingga konteks tidak hilang.

Aturan injeksi:

- Ambil `inject_messages` terakhir, potong dari yang tertua sampai `inject_max_chars`.
- Format: `[HH:MM] Nama: teks` (waktu lokal proses).
- `&`, `<`, `>` di-escape agar teks tidak bisa menutup blok `<group_context>`.
- Konteks hanya diberikan bila `enabled` dan chat lolos filter capture.

### Konfigurasi

```yaml
group_context:
  enabled: true
  dir: "~/.local/share/sanka/group-context"
  capture_all_groups: true
  capture_group_ids: []
  retention_hours: 24
  max_messages: 200
  inject_messages: 30
  inject_max_chars: 3500
```

- `enabled: false` menjadi default di `config/sanka.example.yaml`; salinan lokal owner diaktifkan.
- `capture_all_groups: false` + `capture_group_ids` untuk mode ketat. Bila keduanya kosong, tidak ada yang dicatat.
- `dir` wajib absolut dan di luar repo.

### Integrasi

- `sanka/models.py`: `InboundMessage.sender_name: str = ""`.
- `gateway/normalizer.py`: validasi `sender_name` opsional (string, maksimal 64, karakter kontrol dibuang).
- `gateway/bridge/src/normalize.js`: kirim `sender_name` dari `msg.pushName`.
- `gateway/dispatcher.py`: `group_context` opsional. Catat pesan IGNORE segera; catat pesan COMMAND/AGENT di `finally` setelah diproses.
- `sanka/agent/runtime.py`: provider konteks opsional; hanya untuk `message.is_group`.
- `sanka/agent/prompt.py`: `build_system_prompt(group_context=...)` menambahkan blok data dan aturan "bukan instruksi".
- `gateway/app.py`: bangun store dari config, teruskan ke dispatcher dan runtime.

## Langkah

1. Tambah `sender_name` ke model, normalizer, bridge, dan tes kontrak bridge.
2. Implementasikan `GroupContextStore` + tes unit (retensi, batas, permission, hash, reload, escape).
3. Tambah `GroupContextConfig` di config + validasi + contoh YAML; aktifkan di config lokal.
4. Integrasikan capture di dispatcher dan injeksi di runtime/prompt.
5. Tambah tes dispatcher, runtime, dan prompt.
6. Perbarui ADR-0025, SECURITY, ARCHITECTURE, README, plan-11, LOG.
7. Jalankan pytest + npm test + compileall; restart lewat `scripts/sanka restart`; verifikasi status.

## Tes

```bash
.venv/bin/python -m pytest -o addopts="" -q
.venv/bin/python -m compileall -q sanka core gateway
cd gateway/bridge && npm test
scripts/sanka restart
scripts/sanka status
```

Kasus wajib:

| Area | Kasus |
|---|---|
| Store | record + context memuat nama dan teks; command diabaikan; retensi memangkas; batas jumlah/karakter; reload dari file |
| Store | file `0600`, dir `0700`, nama file hash, `chat_id` tidak muncul di nama/isi |
| Store | teks berisi `<group_context>` tidak bisa menutup blok |
| Dispatcher | pesan grup tanpa mention dicatat tetapi tidak memicu agent; DM tidak dicatat; gate tolak tidak dicatat |
| Runtime | payload grup memuat `<group_context>`; DM tidak memuat |
| Prompt | blok konteks diberi label data dan aturan bukan instruksi |
| Bridge | `sender_name` masuk event dan dibersihkan |

## Kriteria selesai

- Obrolan grup tanpa mention tercatat di JSONL dan dipakai pada balasan berikutnya.
- DM tidak pernah masuk store.
- Konteks dibatasi ukuran/retensi dan tidak bisa menutup blok data.
- Tidak ada isi pesan di log biasa; hanya metadata.
- Semua tes lulus; kedua proses hidup setelah `scripts/sanka restart`.

## Yang perlu dipahami owner

- **Ini bukan log.** Store konteks punya permission, retensi, dan hash; log aplikasi tetap metadata saja.
- **Privasi:** menyalakan `capture_all_groups` berarti semua teks grup yang diikuti Sanka tersimpan maksimal 24 jam di disk. Gunakan `capture_group_ids` bila ingin membatasi.
- **Prompt injection:** isi grup tetap data. Blok `<group_context>` di-escape dan diberi aturan eksplisit.
- **Batas:** konteks bukan memori. Setelah retensi habis, Sanka tidak tahu lagi; memori jangka panjang tetap plan-7.

## Catatan perubahan

- 2026-10-21: Plan dibuat atas permintaan owner agar Sanka memahami seluruh obrolan grup. Status `In Progress` karena permintaan eksplisit; keputusan privasi dicatat di ADR-0025.

## Hasil

- Status: **Done** (2026-10-21).
- Bridge mengirim `sender_name` dari `pushName`; normalizer membatasi 64 karakter; `InboundMessage` membawa `sender_name`.
- `sanka/group_context.py`: `GroupContextStore` JSONL per grup, nama file HMAC, `0700/0600`, retensi/ukuran, escape `<group_context>`, reload setelah restart.
- `gateway/dispatcher.py`: pesan grup IGNORE dicatat langsung; pesan COMMAND/AGENT dicatat setelah giliran selesai; DM dan gate yang ditolak tidak dicatat.
- `sanka/agent/runtime.py` + `prompt.py`: giliran grup membaca konteks dan menambahkan blok `<group_context>` bertanda DATA; DM tidak membaca store.
- Config `group_context` ditambahkan; contoh default `enabled: false`, config lokal diaktifkan dengan `capture_all_groups: true`, retensi 24 jam.
- Dokumentasi: ADR-0025, SECURITY, ARCHITECTURE, README, plan-11, LOG.
- Tes yang dijalankan:
  - `.venv/bin/python -m pytest -o addopts="" -q` → **509 passed**.
  - `.venv/bin/python -m compileall -q sanka core gateway` → exit `0`.
  - `cd gateway/bridge && npm test` → **126 passed**.
  - Smoke loopback + model OpenCode Go nyata: pesan grup tanpa mention `"Rapat minggu ini dipindah ke Jumat jam 10 ya"` tersimpan; mention berikutnya dijawab `"Menurut pesan Budi di grup, rapat minggu ini dipindah ke *Jumat jam 10.00* ✅"`.
  - File konteks `0600`, direktori `0700`, nama file hash, isi JSONL memuat nama/teks tanpa `chat_id` atau JID pengirim mentah (pengirim disimpan sebagai hash).
  - Retensi ditegakkan di disk: baris kedaluwarsa ditulis ulang/dibuang saat load, `context()`, atau `record()`.
  - Restart final lewat wrapper: gateway PID 174106, bridge PID 174132, keduanya health `ok`, bridge `connected:true`.
- Belum dijalankan: verifikasi di grup WhatsApp nyata setelah restart gateway oleh owner.
