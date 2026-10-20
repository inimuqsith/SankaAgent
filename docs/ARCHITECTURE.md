# ARCHITECTURE

Status: rencana awal. Perubahan besar dicatat lewat ADR.

## Alur pesan

```
WhatsApp
   |
[gateway]  (implementasi Sanka: bridge WhatsApp Web + client tipis)
   |  on_message(msg) -> queue
[normalizer]  -> {pengirim, peran, chat_id, is_group, teks, reply_to, mentions}
   |
[router]
   |-- diawali "/"  --> [command registry] --> hasil deterministik (tanpa LLM)
   |
   |-- tanpa "/":
         grup  : diproses hanya jika di-mention / @sanka / di-reply ke Sanka, selain itu diabaikan
         pribadi: selalu diproses
         |
      [policy] cek peran, rate limit
         |
      [agent loop]  (harness turunan GenericAgent)
         |  memanggil tool lewat [tool registry] (allowlist per peran)
         |
      [formatter]  hanya teks final, buang reasoning/tool trace, batasi panjang
   |
[gateway] -> WhatsApp
```

## Komponen

- **gateway**: tanggung jawabnya hanya I/O WhatsApp. Antarmuka ke dalam sengaja tipis: `on_message(msg)`, lalu pesan masuk ke antrean per chat. Jangan ada logika agent di sini. Implementasinya milik Sanka sendiri: proses bridge memegang koneksi WhatsApp Web, client Python menormalkan pesan. Tidak ada kode Hermes yang di-copy.
- **router**: memutuskan jalur command vs LLM dan menerapkan aturan mention di grup.
- **command registry**: tiap command punya nama, izin peran, handler, dan teks bantuan. Lihat `COMMANDS.md`.
- **policy**: peran (owner/panitia/peserta), matriks izin tool, rate limit per pengguna. Penegakan di kode.
- **agent loop**: loop persepsi, pikir, tool, memori (turunan `agent_loop.py` GenericAgent). Sebisa mungkin tidak diubah, diperluas lewat hook.
- **tool registry**: daftar tool bundle. Tool yang tidak diizinkan untuk peran tertentu tidak muncul di daftar tool yang dilihat LLM pada giliran itu.
- **formatter**: satu-satunya jalan keluar ke WhatsApp. Aturannya: teks final saja, ringkas, batas panjang, kecuali jenis respons "rekap" yang boleh panjang dan dipecah per pesan.
- **memori dan skill**: lapisan memori turunan GenericAgent. Self-evolution aktif, belajar dari semua pengguna, dengan pencatatan asal-usul skill. Lihat ADR dan `SECURITY.md`.

## Tool bundle (rencana, 10 tool)

| Tool | Fungsi | Catatan |
|---|---|---|
| web_search | Cari informasi di web | |
| browser | Browser asli (TMWebdriver) | Profil Chrome khusus Sanka |
| sheet_read | Baca Google Sheets | Allowlist file |
| doc_read | Baca PDF, Excel, dokumen lain | |
| recap | Hitung dan susun rekap dari data | Hitung di kode, LLM hanya menarasikan |
| sheet_write | Tulis terbatas ke Workspace | Hanya allowlist |
| proposal_send | Kirim proposal ke owner | Butuh `/approve` |
| code_run, file_write, shell | Kendali sistem | Owner saja, shell butuh toggle config |

## Rekap spreadsheet
1. Tool membaca data mentah.
2. Kode (pandas atau sejenis) menghitung agregat.
3. LLM menerima hasil hitung dan hanya merangkai kalimat/format.
4. Formatter memecah hasil panjang menjadi beberapa pesan.

## Konfigurasi
Satu file konfigurasi non-secret (`config/sanka.yaml`, contoh di `config/sanka.example.yaml`) memuat: model, daftar owner/panitia, allowlist Workspace, toggle shell, batas panjang jawaban, rate limit, session TTL. Secrets (API key, token, kredensial service account, sesi WhatsApp) hanya dari environment variable atau `config/secrets.env` dengan permission 0600. Keduanya di-ignore git. Adapter memetakan YAML + env ke `cfg` yang dipakai `llmcore.py`; tidak ada `mykey.py` duplikat.
