# ADR — Architecture Decision Records

Aturan: append-only. Jangan menimpa ADR lama. Untuk mengubah keputusan, tambah ADR baru dan tandai yang lama `Superseded by ADR-XXXX`.
Status: `Proposed` (usulan, belum disetujui owner), `Accepted`, `Superseded`, `Rejected`.

## Template
```
## ADR-XXXX — Judul
- Status:
- Tanggal:
- Konteks:
- Keputusan:
- Konsekuensi:
```

---

## ADR-0001 — Harness berbasis GenericAgent
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Tidak ingin merancang agent dari nol. GenericAgent kecil (inti sekitar 3K baris, loop sekitar 100 baris), punya memori berlapis dan mekanisme skill.
- Keputusan: Pakai GenericAgent sebagai basis arsitektur dan harness, dimodifikasi penuh.
- Konsekuensi: Perlu memahami `agent_loop.py` dan `llmcore.py`. SOP bawaan berbahasa Mandarin, L0 ditulis ulang dalam bahasa Indonesia. Tool bawaan yang berbahaya untuk bot publik (code_run, kontrol browser/JS, file_write) dibatasi lewat registry (ADR-0006, ADR-0007). Modifikasi dicatat di `UPSTREAM.md`.
- Koreksi 2026-10-20: `docs/UPSTREAM.md` dibatalkan. Modifikasi upstream dicatat lewat komentar `SANKA-MOD:` di kode, ADR terkait, dan `docs/LOG.md`.

## ADR-0002 — Gateway WhatsApp dari Hermes Agent saja
- Status: Superseded by ADR-0013
- Tanggal: 2026-10-20
- Konteks: Owner sudah memakai Hermes Agent dan nyaman dengan gateway WhatsApp-nya.
- Keputusan: Ambil hanya sistem gateway WhatsApp dari Hermes. Bagian lain tidak dipakai. Dimodifikasi agar reasoning dan tool trace tidak dikirim ke WhatsApp.
- Konsekuensi: Perlu memetakan batas antara I/O pesan dan pemanggilan agent di `gateway/` Hermes, lalu membuat antarmuka tipis `on_message(msg) -> reply`. Risiko pemblokiran nomor pada jalur tidak resmi perlu dijaga dengan rate limit.

## ADR-0003 — Routing hybrid: "/" vs LLM
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Butuh kepastian untuk perintah rutin dan kemudahan ngobrol bebas.
- Keputusan: Pesan diawali `/` ditangani command registry deterministik. Pesan tanpa `/` ditangani LLM.
- Konsekuensi: Command registry harus bisa dites tanpa LLM. Perlu daftar command dan izin per peran (`COMMANDS.md`).

## ADR-0004 — Di grup hanya merespons mention
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Merespons semua pesan grup akan berisik dan boros kuota.
- Keputusan: Di grup, Sanka memproses pesan tanpa `/` hanya bila di-mention atau dipanggil `@sanka` (dan reply ke Sanka). Di chat pribadi semua pesan tanpa `/` diproses.
- Konsekuensi: Normalizer harus mendeteksi mention dan reply. Perlu tes untuk variasi penulisan nama.

## ADR-0005 — Tool dibundle native, bukan MCP
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Jumlah tool sedikit (sekitar 5 sampai 6). Lebih sederhana dan cepat bila satu proses.
- Keputusan: Tool berupa fungsi Python dalam proses yang sama. Skema input/output eksplisit agar mudah dibungkus MCP nanti.
- Konsekuensi: Jika kelak dibutuhkan agent lain, tambah lapisan MCP tanpa mengubah logika tool.
- Koreksi 2026-10-20: jumlah tool aktual 10 (lihat tabel di `ARCHITECTURE.md`), bukan 5–6. Keputusan tetap: native bundle.

## ADR-0006 — Izin tool berbasis peran, ditegakkan di kode
- Status: Accepted (prinsip). Detail matriks: lihat ADR-0011.
- Tanggal: 2026-10-20
- Konteks: Prompt saja tidak cukup mencegah penyalahgunaan di grup.
- Keputusan: `code_run`, `file_write` bebas, dan shell hanya untuk owner. Shell juga butuh toggle di konfigurasi. Penegakan di registry tool, bukan hanya prompt.
- Konsekuensi: Tool tak berizin tidak ditawarkan ke LLM pada giliran itu dan ditolak lagi saat eksekusi.
- Koreksi 2026-10-20: `code_run`, `file_write`, dan shell hanya untuk owner. Kata "bebas" pada keputusan di atas berarti bebas bagi owner, bukan untuk peran lain. Shell tetap butuh toggle config.

## ADR-0007 — Tool browser tetap dipertahankan
- Status: Accepted (keputusan). Profil khusus: Proposed.
- Tanggal: 2026-10-20
- Konteks: Owner meminta browser tetap ada. Browser GenericAgent memakai sesi Chrome asli yang menyimpan login.
- Keputusan: Tool browser dipertahankan. Usulan pengamanan: profil Chrome khusus Sanka yang hanya terlogin ke akun yang dibutuhkan, idealnya di mesin terpisah.
- Konsekuensi: Perlu menyiapkan profil dan, bila diterima, memisahkan lingkungan. Siapa saja yang boleh memakai browser ditentukan di ADR-0011.

## ADR-0008 — Self-evolution aktif, belajar dari semua pengguna
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Owner ingin Sanka makin jarang salah lewat belajar dari pengalaman. Agent menyarankan membatasi sumber belajar ke owner/panitia; owner menolak saran itu dan memilih belajar dari semua pengguna, dengan manajemen oleh owner.
- Keputusan: Mekanisme skill/memori GenericAgent diaktifkan dan boleh belajar dari semua pengguna. Owner yang mengurus bila ada masalah.
- Konsekuensi: Ada risiko skill terbentuk dari masukan yang memanipulasi. Mitigasi yang diusulkan (belum disetujui, ADR-0011): catat asal-usul skill, serta `/skill list` dan `/skill rm`.

## ADR-0009 — Jawaban singkat dengan dua lapis
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Pengguna tidak butuh reasoning dan trace tool; instruksi prompt saja sering terlupa.
- Keputusan: Lapis 1: instruksi persona. Lapis 2: formatter di kode hanya meneruskan teks final, menekan progres tool (cukup indikator mengetik), dan membatasi panjang kecuali jenis respons rekap. `/verbose` untuk owner.
- Konsekuensi: Perlu penanda jenis respons "rekap" di pipeline.

## ADR-0010 — Rekap dihitung di kode
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Angka salah pada acara berdampak nyata.
- Keputusan: Agregasi dilakukan di kode. LLM hanya merangkai kalimat dari hasil hitung.
- Konsekuensi: Tool recap harus mengembalikan hasil terstruktur dan teruji.

## ADR-0011 — Matriks izin tool, asal-usul skill, dan akses Workspace
- Status: Proposed
- Tanggal: 2026-10-20
- Konteks: Matriks izin per peran belum diputuskan. Beberapa pengamanan baru berupa saran.
- Keputusan (usulan):
  1. Matriks izin seperti di `SECURITY.md`.
  2. Catat asal-usul setiap skill dan sediakan `/skill list` dan `/skill rm` untuk owner.
  3. Akses Workspace lewat service account dengan allowlist file/folder. Read default, write terbatas.
  4. Pengiriman proposal butuh `/approve` dari owner.
- Konsekuensi: Perlu persetujuan owner. Setelah disetujui, ubah status menjadi `Accepted` lewat ADR turunan atau catatan di LOG.

## ADR-0012 — LLM: OpenCode Go
- Status: Accepted (pilihan penyedia). Detail API: belum diverifikasi.
- Tanggal: 2026-10-20
- Konteks: Owner memakai inferensi OpenCode Go.
- Keputusan: LLM utama memakai OpenCode Go.
- Konsekuensi: Format API (apakah kompatibel dengan format OpenAI) belum diverifikasi. Diverifikasi di plan-2 sebelum menulis adapter di `llmcore.py`.
- Catatan verifikasi 2026-10-20: OpenCode Go adalah langganan nyata ($10/$40 per bulan) yang ditujukan untuk OpenCode dan coding agent sejenis; traffic dipantau untuk penyalahgunaan. Limit per model: 5 jam = 20%, minggu = 50%, bulan = 100% dari kuota bulanan. Klien disarankan mengirim `x-opencode-session` dan user-agent sendiri. Bot WhatsApp 24/7 berisiko kena limit atau dianggap traffic tidak wajar. Wajib diverifikasi di plan-2: base URL, apakah API key Go berlaku di luar OpenCode, dan provider fallback.


## ADR-0013 — Gateway WhatsApp: ambil yang berguna dari Hermes, adaptasi, jangan vendor mentah
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: ADR-0002 memilih "ambil hanya sistem gateway WhatsApp dari Hermes". Audit 2026-10-20 menunjukkan adapter WhatsApp Hermes adalah plugin yang bergantung pada `BasePlatformAdapter` dan core gateway yang besar. Owner mengklarifikasi: bukan berarti tidak mengambil sama sekali; ambil kode yang berguna bila bisa, lalu ubah dan adaptasi. Yang tidak boleh adalah menyalin mentah tanpa perubahan.
- Keputusan:
  1. Kode dan pola Hermes boleh diambil sebagian bila memberi nilai. Setiap bagian yang diambil diubah/diadaptasi, mempertahankan notice MIT, dan ditandai `SANKA-MOD:`.
  2. `gateway/` adalah bagian Sanka sendiri dengan pola bridge: proses terpisah memegang koneksi WhatsApp Web (Baileys, MIT), lalu berkomunikasi dengan proses Python lewat HTTP lokal. Potongan kode bridge, allowlist, atau gating Hermes boleh dipakai ulang setelah diadaptasi.
  3. Perilaku yang diadopsi dari Hermes: reconnect dengan backoff, session persistence di luar repo, long-poll inbound, endpoint kirim/typing, allowlist dan gating grup, deteksi mention/reply, idempotensi kirim, dan rate limit.
  4. Antarmuka ke inti Sanka tetap tipis: `on_message(msg)` menaruh pesan di antrean async per chat; balasan dikirim lewat gateway tanpa membawa reasoning atau trace tool.
  5. Atribusi Hermes dan Baileys dicantumkan di `README.md`. Notice MIT upstream wajib dipertahankan untuk kode yang dipakai ulang.
  6. WhatsApp Cloud API resmi tetap opsi cadangan bila risiko blokir nomor pada jalur tidak resmi tidak dapat diterima. Perpindahan jalur dicatat sebagai ADR baru.
- Konsekuensi: Tidak harus ada dependency ke paket Hermes; kode yang diadaptasi menjadi bagian repo Sanka. Setiap adaptasi dicatat di ADR/LOG dan dicek kepatuhan lisensinya saat review. Risiko pemblokiran nomor pada jalur Baileys tetap ada dan dimitigasi dengan rate limit serta perilaku non-spam.

## ADR-0014 — Konfigurasi: satu YAML non-secret, secrets lewat env
- Status: Accepted
- Tanggal: 2026-10-20
- Konteks: Dokumen sebelumnya menyebut dua sumber config (`sanka.yaml` dan `mykey.py`) sehingga rawan duplikasi dan kebocoran secrets.
- Keputusan:
  1. `config/sanka.yaml` adalah satu-satunya sumber konfigurasi non-secret: model, daftar owner/panitia, allowlist Workspace, toggle shell, rate limit, batas panjang jawaban, session TTL.
  2. Secrets (API key, token, kredensial service account, sesi WhatsApp) hanya dari environment variable atau `config/secrets.env` dengan permission 0600. Keduanya di-ignore git.
  3. `config/sanka.example.yaml` dan `config/secrets.example.env` disediakan sebagai contoh, tanpa nilai rahasia.
  4. Adapter Sanka memetakan YAML + env ke bentuk `cfg` yang diharapkan `llmcore.py`; tidak ada `mykey.py` duplikat.
- Konsekuensi: Startup harus gagal cepat bila env wajib tidak ada. Owner hanya perlu mengelola satu file config non-secret.
