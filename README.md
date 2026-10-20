# SankaAgent (Sanka)

**Si Kecil Penjelajah Sanankerto** — asisten acara berbasis AI untuk WhatsApp.
Dikembangkan oleh Abdul Muqsith. Ditenagai AMQS Studio.

> Status: tahap arsitektur. Belum ada kode aplikasi. Dokumen keputusan ada di `docs/`.

## Cara kerja

- Pesan diawali `/` = command deterministik, tanpa LLM.
- Pesan tanpa `/` = percakapan dengan AI.
- Di grup, Sanka menjawab hanya saat di-mention, dipanggil `@sanka`, atau di-reply.
- Kemampuan utama: rekap spreadsheet, baca PDF/Excel, web search, bantu panitia, kirim proposal ke owner.

## Mulai

Baca berurutan:

1. `AGENTS.md` — aturan kerja, batasan keras, struktur repo.
2. `docs/ADR.md` — keputusan arsitektur.
3. `docs/LOG.md` — posisi pekerjaan terakhir.
4. `docs/plan/` — plan aktif.

Konfigurasi non-secret: `config/sanka.example.yaml`.
Secrets: environment variable atau `config/secrets.env` (0600, di-ignore git).

## Struktur

```text
core/          harness berbasis GenericAgent
gateway/       gateway WhatsApp implementasi Sanka
sanka/         router, commands, policy, formatter, tools, persona
tests/         tes tanpa LLM dan tanpa WhatsApp
docs/          arsitektur, keamanan, persona, command, ADR, LOG, plan
references/    clone upstream untuk studi; di-ignore git
```

## Lisensi

SankaAgent dilisensikan di bawah **MIT License**. Teks lengkap ada di [`LICENSE`](LICENSE).
Copyright (c) 2026 Abdul Muqsith.

## Atribusi dan kepatuhan lisensi

Sanka belajar dari proyek MIT di bawah ini. **Tidak ada source code upstream yang di-vendor mentah**; clone hanya dipakai sebagai referensi di `references/` yang di-ignore git. Bila kelak ada potongan kode yang benar-benar dipakai ulang, notice copyright aslinya dipertahankan dan modifikasinya ditandai `SANKA-MOD:`.

| Proyek | Pemilik | Lisensi | Peran untuk Sanka |
|---|---|---|---|
| [GenericAgent](https://github.com/lsdefine/GenericAgent) | lsdefine, Copyright (c) 2025 | MIT | Basis arsitektur dan harness: agent loop, layered memory, skill |
| [Hermes Agent](https://github.com/NousResearch/hermes-agent) | Nous Research, Copyright (c) 2025 | MIT | Referensi perilaku gateway WhatsApp; bukan kode yang di-copy |
| [Baileys](https://github.com/WhiskeySockets/Baileys) | Rajeh Taher/WhiskeySockets, Copyright (c) 2025 | MIT | Library koneksi WhatsApp Web untuk bridge milik Sanka |

Referensi yang dipelajari (commit saat audit 2026-10-20):

- GenericAgent `e6b1bcec92e7789eb80975980b5fd47a2abea860`
- Hermes Agent `0319e113c9602b86ffda350e931cb4b035e1d5d8`

Teks lisensi lengkap tiap proyek tersedia di `LICENSE` masing-masing di dalam clone `references/` dan di repositori resminya. Atribusi ini wajib dipertahankan di distribusi source maupun binary.

## Dokumen

- `AGENTS.md` — aturan repo
- `docs/ARCHITECTURE.md` — alur dan komponen
- `docs/SECURITY.md` — model ancaman, peran, matriks izin
- `docs/COMMANDS.md` — daftar command
- `docs/PERSONA.md` — karakter dan L0
- `docs/ADR.md` — keputusan arsitektur
- `docs/LOG.md` — catatan sesi kerja
- `docs/plan/README.md` — template plan
