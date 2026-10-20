# Plan 8 — Hardening, tool istimewa, dan deployment

- Status: Draft
- Tanggal: 2026-10-21
- Pemilik: Abdul Muqsith

## Tujuan

Membuat Sanka bisa dijalankan 24/7 dengan aman: tool istimewa (`browser`, `code_run`, `file_write`, shell) hanya untuk owner dan dibatasi sandbox, secret dikelola dan dirotasi tanpa menyentuh git, log tidak memuat isi pesan pribadi atau secret, setiap aksi istimewa punya jejak audit, proses berjalan di bawah supervisor dan pulih setelah restart, serta ada prosedur uji beban, runbook insiden, dan checklist rilis yang bisa diulang owner.

Plan ini **tidak menambah fitur baru**. Semua yang ada di sini hanya membuat hasil plan-1 sampai plan-7 bisa dioperasikan dengan aman. Bila sebuah kebutuhan ternyata adalah fitur (misalnya browser interaktif dengan klik dan isi form), hal itu dicatat sebagai usulan dan tidak dikerjakan di sini.

## Di luar cakupan

- Fitur baru: browser interaktif (klik, isi form, unggah berkas), scheduler, dashboard web, multi-tenant, multi-bot, MCP.
- Persistensi sesi percakapan ke disk. Sesi tetap in-memory (ADR-0016); restart menghapus konteks chat. Plan ini hanya memastikan restart tidak merusak data lain dan tidak mengirim balasan ganda.
- Antrean keluar yang persisten. Balasan yang belum terkirim saat proses mati hilang; ini dicatat sebagai risiko, bukan dikerjakan.
- Isolasi tingkat kernel (container per tool, gVisor, Firecracker, VM terpisah untuk shell). Plan-8 memakai sandbox proses POSIX + systemd hardening. Isolasi lebih kuat dibahas di "Risiko".
- Rotasi otomatis kredensial Google dan pairing ulang WhatsApp. Rotasi di sini adalah prosedur manual yang dipandu CLI.
- Perubahan pada router, policy peran, formatter, prompt persona, dan skema tool baca/tulis dari plan-5/plan-6. Plan-8 hanya menambah tool istimewa dan lapisan operasional.
- Enkripsi backup sebagai keharusan. Enkripsi didukung bila `age` tersedia, tetapi bukan syarat.

## Prasyarat

1. Plan-1 sampai plan-7 sudah `Done`. Plan-8 bergantung pada modul yang dibuat di sana dan **tidak boleh mengklaim modul itu sudah ada**:
   - `sanka/config.py`, `sanka/policy.py` (plan-1) — config, `Role`, `TOOL_PERMISSIONS`, `can_use_tool`.
   - `sanka/secrets.py`, `sanka/queue.py`, `sanka/sessions.py`, `sanka/agent/*` (plan-2).
   - `gateway/*` beserta server HTTP lokal dan normalizer (plan-3).
   - `gateway/bridge` Node/Baileys dengan endpoint kirim/typing (plan-4).
   - `sanka/tools/registry.py` dengan penegakan izin ganda (plan-5).
   - Alur proposal + `sanka/audit.py` (plan-6).
   - `memory/` dan skill dengan pencatatan asal-usul (plan-7).
2. ADR-0011 (matriks izin) dan ADR-0015 (Python 3.12) sudah `Accepted`.
3. ADR baru yang diajukan plan ini sudah disetujui owner (lihat "Dependency baru"):
   - ADR-0022 — Sandbox tool istimewa: proses terpisah, rlimit, timeout, batas output.
   - ADR-0023 — Supervisor: systemd sebagai jalur utama, container sebagai alternatif.
   - ADR-0024 — Audit trail dan redaksi log.
4. Host target: Linux dengan systemd >= 249, Python 3.12, Node.js LTS. Chromium hanya bila `tools.browser_enabled` dinyalakan.
5. Owner sudah punya salinan `config/secrets.env` (0600) dan kunci OpenCode Go yang valid, serta akses `sudo` untuk memasang unit systemd.

## Rancangan

### Struktur file

```text
sanka/
  ops/
    __init__.py
    lifecycle.py         # Lifecycle: sinyal, graceful shutdown, watchdog
    health.py            # build_health()/build_ready(); dipasang oleh server plan-3
    notify.py            # sd_notify() stdlib (NOTIFY_SOCKET), no-op bila tidak ada
    doctor.py            # pemeriksaan config/secret/direktori/permission/toggle
    cli.py               # python -m sanka.ops {doctor,health,rotate-check}
  logs.py                # Redactor, configure_logging, log_event (JSON)
  audit.py               # perluasan plan-6: JsonlAuditSink, rotasi, HMAC id
  tools/
    privileged.py        # satu-satunya jalan eksekusi 4 tool istimewa
    sandbox.py           # run_process(): rlimit, timeout, batas output, kill group
    _limits.py           # wrapper launcher yang memasang rlimit lalu exec
    browser.py           # browser_read(): chromium satu tembakan, profil khusus
    schema.py            # skema input/output 4 tool istimewa (bila plan-5 belum punya)
tests/
  test_tools_permission.py
  test_shell_toggle.py
  test_sandbox.py
  test_file_write.py
  test_browser_tool.py
  test_redact.py
  test_audit.py
  test_secrets_reload.py
  test_health.py
  test_lifecycle.py
  test_doctor.py
deploy/
  systemd/
    sanka.service
    sanka-bridge.service
    sanka-backup.service
    sanka-backup.timer
  Containerfile
  compose.yaml
  backup.sh
  restore.sh
  install.sh            # idempoten: venv, permission, symlink rilis, unit
  sanka.env.example     # EnvironmentFile contoh, tanpa secret
scripts/
  loadtest.py           # beban + soak, bridge & LLM palsu
docs/
  RUNBOOK.md            # runbook insiden
  RELEASE.md            # checklist rilis dan rollback
  SECURITY.md           # tambah bagian sandbox, audit, rotasi secret
```

### Tata letak runtime

```text
/opt/sanka/releases/<versi>/      # checkout read-only untuk user sanka
/opt/sanka/current -> releases/   # symlink yang dipakai unit
/etc/sanka/sanka.yaml             # 0640 root:sanka, non-secret
/etc/sanka/secrets.env            # 0600 sanka:sanka, di luar git
/var/lib/sanka/audit/             # 0700, JSONL bulanan 0600
/var/lib/sanka/memory/            # plan-7
/var/lib/sanka/workspace/         # cwd code_run/shell dan root file_write
/var/lib/sanka/browser-profile/   # 0700, profil Chromium khusus
/var/log/sanka/                   # hanya bila journald tidak dipakai
```

Repo tetap memakai `config/sanka.yaml` untuk pengembangan. Di produksi, unit mengirim `--config /etc/sanka/sanka.yaml`; config repo tidak dibaca.

### Tool istimewa

Empat tool, satu jalur eksekusi. Tidak ada tool istimewa yang dieksekusi langsung dari registry.

```text
LLM tool_call
  -> tool registry (plan-5): tool tidak berizin tidak ditawarkan
  -> registry exec check: policy.can_use_tool(role, tool, shell_enabled=cfg.tools.shell_enabled)
  -> sanka.tools.privileged.run(ctx, name, args)
       gate 1: role == OWNER (literal, bukan "bukan peserta")
       gate 2: toggle tool di config, dibaca ulang dari sumber config saat panggilan
       gate 3: batas argumen (ukuran, path, skema) divalidasi sebelum proses dibuat
  -> sandbox.run_process() / file_write / browser
  -> ToolResult(ok, output, truncated, error_code)
  -> audit.record(...) untuk allow maupun deny
```

Gate 2 membaca `ToolsConfig` lewat `ConfigProvider.current()` (sumber yang sama dengan reload SIGHUP). Alasannya: mematikan shell harus berlaku pada panggilan berikutnya, bukan setelah restart. Bila reload belum tersedia, gate 2 minimal membandingkan `mtime` file config dengan salinan terakhir yang dimuat.

#### Matriks izin (penegakan berlapis)

| Tool | owner | panitia | peserta | Toggle config | Batas eksekusi |
|---|---|---|---|---|---|
| `browser_read` | ya | tidak | tidak | `tools.browser_enabled` | timeout 30 s, output 2 MB |
| `code_run` | ya | tidak | tidak | `tools.code_run_enabled` | timeout 30 s, output 64 KB |
| `file_write` | ya | tidak | tidak | `tools.file_write_enabled` | 256 KB/berkas, root terbatas |
| `shell` | ya | tidak | tidak | `tools.shell_enabled` | timeout 30 s, output 64 KB |

Tidak ada pengecualian. Toggle hanya bisa **mengurangi** izin, tidak pernah menambah: `shell_enabled: true` untuk panitia tetap ditolak karena gate 1 memeriksa peran lebih dulu.

#### `sanka/tools/sandbox.py`

```python
@dataclass(frozen=True, slots=True)
class SandboxLimits:
    timeout_seconds: float
    max_output_bytes: int
    cpu_seconds: int
    memory_bytes: int
    max_file_bytes: int
    max_open_files: int
    max_processes: int

@dataclass(frozen=True, slots=True)
class ProcessResult:
    exit_code: int | None
    stdout: str
    stderr: str
    truncated: bool
    timed_out: bool
    duration_ms: int

def run_process(
    argv: Sequence[str],
    *,
    cwd: Path,
    env: Mapping[str, str],
    limits: SandboxLimits,
    stdin_text: str | None = None,
) -> ProcessResult: ...
```

Keputusan teknis:

- Proses dijalankan lewat `sanka.tools._limits` (`sys.executable -I <path>/_limits.py --cpu --as --fsize --nofile --nproc -- <argv>`), yang memasang `resource.setrlimit` lalu `os.execv`. Alasannya: `preexec_fn` tidak aman di proses ber-thread (server plan-3 memakai thread), dan Python 3.11+ belum punya parameter rlimit di `subprocess`.
- `start_new_session=True` agar timeout bisa mematikan seluruh process group (`os.killpg(pid, SIGKILL)`), termasuk anak proses.
- `env` dibersihkan: hanya `PATH`, `HOME` (ke workspace), `LANG`, `TZ`, dan variabel yang secara eksplisit diizinkan tool. **Tidak ada** `OPENCODE_GO_API_KEY`, `GOOGLE_SERVICE_ACCOUNT_JSON`, `SANKA_AUDIT_HMAC_KEY`, atau isi `secrets.env`.
- Output dibaca dengan batas; bila melewati `max_output_bytes`, proses dimatikan dan hasil ditandai `truncated=True`. Tidak ada buffering tak terbatas.
- `stdin_text` dibatasi 64 KB; `None` berarti `/dev/null`.
- Semua kegagalan menjadi `ToolResult` dengan `error_code`: `timeout`, `output_limit`, `exit_nonzero`, `denied`, `invalid_args`, `spawn_failed`.

#### `code_run`

- Argumen: `language` (hanya `"python"` di plan-8), `source` (<= 64 KB).
- Ditulis ke `tempfile.mkdtemp(dir=workspace/.tmp)` dengan mode 0600, dijalankan `python -I main.py` di direktori itu, lalu direktori dihapus.
- `max_turns` agent tetap berlaku; satu `code_run` = satu eksekusi, tanpa state antar panggilan.
- Bahasa lain (`bash`, `node`) **tidak** dibuka di sini; `shell` sudah menutup kebutuhan itu untuk owner.

#### `shell`

- Argumen: `command` (<= 4 KB), dijalankan `/bin/sh -c` dengan cwd `tools.workspace_root`.
- Ditolak bila `tools.shell_enabled` bukan `true` pada pembacaan config terakhir.
- Tidak ada allowlist perintah, karena allowlist teks mudah dilewati dan memberi rasa aman palsu. Batas nyata: peran owner, toggle, rlimit, timeout, dan audit.
- Perintah yang jelas destruktif (`rm -rf /`, `mkfs`, `dd of=/dev/`) tidak diblokir di kode; AGENTS.md mewajibkan konfirmasi owner, dan plan ini menambahkan catatan audit `shell` dengan perintah ter-redaksi. Ini keputusan jujur: pemblokiran pola bukan kontrol keamanan.

#### `file_write`

- Argumen: `path` (relatif terhadap `tools.file_write_root`), `content` (<= 256 KB).
- Penolakan: path absolut, `..`, hasil `resolve()` di luar root, symlink yang menunjuk keluar root, target adalah direktori, target cocok pola terlarang (`.git/`, `*.pem`, `*.key`, `secrets.env`, `service-account*.json`, `id_rsa*`).
- Tulisan atomik: tulis ke `.<nama>.tmp` di direktori yang sama, `fsync`, `os.replace`, mode 0640. Tidak ada penulisan sebagian.
- `file_write` tidak boleh menulis ke `/etc/sanka`, `/opt/sanka`, atau `/var/lib/sanka/audit`; root divalidasi saat startup dan saat panggilan.

#### `browser_read`

- Argumen: `url` (http/https saja, <= 2 KB). Host publik; `localhost`, `127.0.0.0/8`, `169.254.0.0/16`, dan `file://` ditolak (mencegah SSRF ke server internal).
- Dijalankan satu tembakan: `<browser_binary> --headless=new --disable-gpu --no-first-run --user-data-dir=<profile> --dump-dom <url>` dengan timeout 30 s dan output 2 MB. Hasil DOM diubah menjadi teks sederhana (stdlib `html.parser`), bukan HTML mentah, agar hemat token.
- Profil khusus dari `tools.browser_profile_dir`; startup gagal bila direktori itu sama dengan profil pribadi owner atau berada di dalam home owner. Direktori harus 0700 dan dimiliki user service.
- Tidak ada klik, isi form, atau unggah berkas. Bila nanti dibutuhkan, itu plan baru dengan ADR baru.
- `browser_binary` kosong atau tidak ada → `error_code=spawn_failed`, bukan exception yang bocor ke pengguna.

#### Skema ke LLM dan penolakan runtime

Skema tool istimewa hanya dikirim ke LLM bila `can_use_tool(role, ...)` benar; untuk panitia/peserta daftar tool tidak memuatnya sama sekali sehingga model tidak bisa memanggilnya. Bila model tetap mengirim `tool_call` (misalnya dari riwayat lama atau prompt injection), `privileged.run()` menolak dengan `denied` dan mencatat audit. Tes `test_tools_permission.py` membuktikan kedua lapisan.

### Secret: pemuatan, validasi, dan rotasi

`sanka/secrets.py` (plan-2) membaca env lebih dulu, lalu `config/secrets.env`, dan menolak permission lebih longgar dari 0600 — itu prasyarat, bukan hasil plan ini. Plan-8 menambah:

```python
class SecretProvider:
    def load(self) -> Mapping[str, str]: ...     # validasi permission + key wajib
    def get(self, name: str) -> str: ...
    def reload_if_changed(self) -> bool: ...     # bandingkan mtime + ukuran
    def fingerprint(self, name: str) -> str: ... # sha256[:8] untuk /status
```

- `fingerprint()` adalah satu-satunya bentuk secret yang boleh muncul di log atau `/status`. Nilainya tidak bisa dibalik.
- Reload dipicu SIGHUP: `Lifecycle` memanggil `reload_if_changed()`; bila file berubah, provider mengganti isi dan `Redactor` mendaftarkan nilai baru **sebelum** nilai lama dihapus dari daftar redaksi (nilai lama tetap diredaksi sampai proses berhenti, agar tidak ada jendela bocor).
- Key wajib divalidasi saat startup dan setelah reload. Gagal validasi → `readyz` 503 dan proses tetap hidup (tidak crash-loop), agar operator bisa memperbaiki file.

Rotasi (prosedur, dijalankan owner):

1. Terbitkan kunci baru di penyedia (OpenCode Go) atau buat service account baru.
2. Tulis `config/secrets.env` baru secara atomik (0600) — jangan edit di tempat dengan editor yang bisa meninggalkan file sementara.
3. `kill -HUP <pid utama>`.
4. `python -m sanka.ops doctor` dan `curl /readyz`; cek `fingerprint` baru di `/status` (owner).
5. Cabut kunci lama setelah 24 jam tanpa error. Catat di `docs/LOG.md` (tanggal, fingerprint, bukan nilainya).

Kunci audit: `SANKA_AUDIT_HMAC_KEY` (32 byte acak, base64) dibuat saat instalasi oleh `deploy/install.sh` bila belum ada, disimpan di `secrets.env`. Tanpa kunci ini audit menolak start (fail closed), karena id tanpa HMAC tidak bisa diredaksi.

### Log dan redaksi

`sanka/logs.py`:

```python
class Redactor:
    def add_secret(self, value: str) -> None: ...
    def redact(self, text: str) -> str: ...

def configure_logging(level: str, *, redactor: Redactor, stream: IO[str] | None = None) -> None: ...
def log_event(event: str, **fields: object) -> None: ...
```

- Format JSON satu baris per event, dikirim ke stderr (systemd → journald). Tidak ada file log ganda secara default.
- Retensi log diatur di host, bukan di aplikasi: `SystemMaxUse=500M` dan `MaxRetentionSec=30day` pada `journald.conf`. Log bukan tempat menyimpan riwayat bisnis; audit yang menyimpan jejak aksi.
- Field yang diizinkan: `ts`, `level`, `event`, `role`, `chat_kind` (`dm`/`group`), `tool`, `duration_ms`, `error_code`, `count`, `decision`, `release`. Field tak dikenal dibuang, bukan dicetak.
- **Isi pesan tidak pernah dicatat.** Tidak ada `text`, `message`, `content`, `prompt`, `reply`, `subject`, `filename` di skema log. Bila sebuah event butuh konteks, ia memakai `chat_hash` (HMAC 16 hex) dan `sender_hash`.
- `Redactor` melakukan dua hal: (a) mengganti nilai secret yang dikenal dengan `[REDACTED:<fingerprint>]`, (b) regex untuk pola umum (`Bearer\s+\S+`, `sk-...`, `AIza...`, `-----BEGIN .*PRIVATE KEY-----`, nomor telepon panjang). Regex adalah jaring pengaman, bukan pengganti (a).
- Nomor telepon di log selalu disamarkan: tiga digit terakhir saja (`***123`). Nomor grup/JID di-hash.
- Handler menangkap exception library dan meredaksinya sebelum dicetak; `httpx` tidak di-log pada level header.
- `/verbose` (owner) menampilkan nama tool dan durasi di WhatsApp, bukan isi log dan bukan prompt.

### Audit trail

`sanka/audit.py` dibuat di plan-6 untuk alur proposal; plan-8 menambah backend berkas, rotasi, dan HMAC tanpa mengubah pemanggil yang sudah ada. Aturan yang berlaku setelah plan-8:

```python
class AuditLog:
    def record(
        self,
        action: str,                 # "tool.call", "tool.deny", "config.reload", "secrets.reload", "shutdown", "deploy.start"
        *,
        role: Role,
        sender_id: str,
        chat_id: str,
        tool: str | None = None,
        target: str | None = None,   # path relatif, host URL, atau nama resource
        decision: str = "allow",     # allow | deny | error
        reason: str | None = None,
        result: str | None = None,   # ok | timeout | truncated | error
        duration_ms: int | None = None,
    ) -> None: ...
```

- Berkas: `audit-YYYY-MM.jsonl`, satu JSON per baris, dibuka `O_APPEND|O_CREAT` mode 0600, direktori 0700. Rotasi bulanan saat startup dan saat pergantian bulan (dicek per event).
- `sender_id` dan `chat_id` **tidak disimpan mentah**: disimpan sebagai `sender_hash`/`chat_hash` = HMAC-SHA256(`SANKA_AUDIT_HMAC_KEY`, id)[:16]. Cukup untuk menghubungkan kejadian dalam satu instalasi, tidak membocorkan nomor bila berkas bocor.
- `target` untuk `file_write` adalah path relatif; untuk `browser_read` hanya host + path tanpa query (query bisa memuat token); untuk `shell`/`code_run` hanya 80 karakter pertama setelah diredaksi, atau `<redacted>` bila memuat pola secret.
- Audit **gagal tertutup** untuk aksi istimewa: bila berkas audit tidak bisa ditulis, tool istimewa ditolak (`error_code=audit_unavailable`). Aksi non-istimewa tetap jalan dengan `log_event(level="error")`.
- Tidak ada endpoint yang menghapus audit. Retensi 12 bulan, pengarsipan manual oleh owner.

### Health check

`sanka/ops/health.py`, dipasang pada server HTTP lokal plan-3 (loopback saja, `ops.health_bind`/`ops.health_port`):

```python
@dataclass(frozen=True, slots=True)
class HealthReport:
    status: str          # "ok" | "degraded" | "fail"
    checks: Mapping[str, str]
    release: str
    uptime_seconds: int

def build_health() -> HealthReport: ...   # proses hidup, config termuat, audit bisa ditulis
def build_ready(deps: ReadyDeps) -> tuple[int, HealthReport]: ...  # 200 | 503
```

| Endpoint | Isi | 200 bila |
|---|---|---|
| `GET /healthz` | proses, config, audit | proses hidup dan config valid |
| `GET /readyz` | + bridge, LLM secret, disk, antrean, sesi | semua wajib hijau |

- Body hanya JSON pendek: `status`, `release`, `checks` berisi nama check dan `ok|fail`, tanpa pesan error mentah, tanpa path absolut, tanpa fingerprint secret.
- `/readyz` mengembalikan 503 bila bridge tidak terjangkau > 60 s, secret wajib tidak ada, ruang disk < 500 MB, atau antrean chat aktif sudah di batas.
- systemd watchdog: `WatchdogSec=30`, aplikasi mengirim `WATCHDOG=1` setiap 10 s setelah health tick berhasil. Bila loop utama macet, systemd me-restart proses.

### Supervisor dan graceful shutdown

Unit utama (ringkas; directive lengkap di `deploy/systemd/sanka.service`):

```ini
[Service]
Type=notify
NotifyAccess=main
User=sanka
Group=sanka
EnvironmentFile=/etc/sanka/env
ExecStart=/opt/sanka/current/.venv/bin/python -m sanka.app --config /etc/sanka/sanka.yaml  # entrypoint dari plan-3
Restart=on-failure
RestartSec=5
StartLimitIntervalSec=120
StartLimitBurst=5
TimeoutStopSec=30
WatchdogSec=30
NoNewPrivileges=yes
PrivateTmp=yes
PrivateDevices=yes
ProtectSystem=strict
ProtectHome=read-only
ReadWritePaths=/var/lib/sanka
RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX
RestrictNamespaces=yes
LockPersonality=yes
MemoryDenyWriteExecute=yes
SystemCallFilter=@system-service
CapabilityBoundingSet=
MemoryMax=1G
TasksMax=256
UMask=0027
```

Catatan: `MemoryDenyWriteExecute` dapat memblokir JIT di beberapa library; diverifikasi di langkah 10. Bila bermasalah, directive dicabut dengan catatan di LOG (bukan diam-diam).

`sanka-bridge.service` untuk proses Node dari plan-4: `Type=simple`, `Restart=always`, sesi WhatsApp di `/var/lib/sanka/whatsapp`, `ReadWritePaths` hanya direktori itu, listen `127.0.0.1` saja.

`sanka/ops/lifecycle.py`:

```python
class Lifecycle:
    def __init__(self, *, server, runtime, queue, audit, redactor, secrets, clock) -> None: ...
    def install(self) -> None: ...                 # SIGTERM, SIGINT, SIGHUP, SIGUSR1
    def shutdown(self, reason: str, *, grace_seconds: float) -> ShutdownReport: ...
    def watchdog_tick(self) -> None: ...
```

Urutan shutdown (dipicu SIGTERM):

1. Berhenti menerima pesan masuk (`server.stop_accepting()`), endpoint kirim mengembalikan 503 agar bridge menahan diri.
2. Set `SIGTERM` kedua = keluar paksa dengan kode 1 (operator bisa mempercepat).
3. Tunggu pekerjaan aktif selesai maksimal `limits.shutdown_grace_seconds` (default 20). Pekerjaan yang masih antre dibatalkan dan pengirimnya tidak dibalas (dicatat `shutdown` di audit dengan jumlah).
4. Tutup `httpx.Client`, flush audit (`os.fsync`), tutup berkas log.
5. `sd_notify("STOPPING=1")` lalu keluar 0.

Pemulihan setelah restart:

- Lock file `flock` di `/var/lib/sanka/sanka.lock` mencegah dua instance (systemd + manual) berjalan bersamaan.
- Saat start: validasi config + secret (fail fast, kode 78 `EX_CONFIG` untuk config, 77 `EX_NOPERM` untuk permission secret), verifikasi direktori runtime, rotasi audit bulanan, hapus `workspace/.tmp/*` lebih tua dari 1 jam, lalu siap.
- Sesi chat hilang (ADR-0016). Bridge punya antrean kirim sendiri; pesan masuk yang belum diproses tidak diputar ulang, jadi tidak ada balasan ganda. Ini batas jujur plan-8.
- Pesan masuk duplikat setelah reconnect bridge ditolak oleh dedupe `message_id` di plan-3 (masih in-memory, lihat Risiko).

### Container (alternatif)

`deploy/Containerfile` (python:3.12-slim, user `sanka` uid 10001, `--read-only` rootfs + tmpfs `/tmp`, volume `/var/lib/sanka` dan `/etc/sanka:ro`, `HEALTHCHECK` memanggil `/healthz` lewat stdlib Python, bukan curl). `deploy/compose.yaml` menjalankan `sanka` + `sanka-bridge` dengan network internal dan hanya bridge yang butuh egress. Container **tidak** menggantikan unit systemd pada host yang sama; pilih salah satu agar tidak ada dua penjadwal.

### Backup dan restore

`deploy/backup.sh` (bash, `set -euo pipefail`, idempoten):

- Isi: `config/sanka.yaml`, `config/secrets.env` (terpisah, dienkripsi), `memory/`, `audit/`, `workspace/` (opsional, default tidak), `sanka.example.yaml` untuk referensi. **Tidak** mem-backup `whatsapp` session ke lokasi yang sama dengan secrets tanpa enkripsi.
- Keluaran: `/var/backups/sanka/sanka-YYYYMMDDTHHMMSSZ.tar.gz` + `.sha256`, mode 0600.
- Retensi: 14 harian + 8 mingguan; rotasi menghapus yang tertua, tidak pernah menghapus backup < 24 jam.
- Enkripsi: bila `age` ada dan `SANKA_BACKUP_RECIPIENT` di-set, hasil dienkripsi (`*.tar.gz.age`); kunci privat **tidak** disimpan di host.
- Timer: `sanka-backup.timer` harian 02:30 dengan `Persistent=true`.
- `restore.sh` wajib memverifikasi checksum, menampilkan daftar isi, meminta konfirmasi eksplisit (`--yes` untuk non-interaktif), berhenti bila service aktif, dan mem-backup keadaan sekarang sebelum menimpa.
- Uji restore dilakukan minimal sekali sebelum rilis pertama dan dicatat di LOG (waktu, ukuran, isi yang dipulihkan).

### Uji beban dan soak

`scripts/loadtest.py` memakai bridge palsu dan `httpx.MockTransport` untuk LLM, sehingga tidak memakai kuota dan tidak menyentuh WhatsApp.

Skenario:

| Skenario | Beban | Durasi | Kriteria |
|---|---|---|---|
| Beban puncak | 60 pesan/menit, 20 chat | 5 menit | p95 balasan < 3 s (latensi LLM palsu 120 ms), 0 error 5xx |
| Beban melebihi batas | 300 pesan/menit | 3 menit | rate limit menolak dengan balasan singkat; tidak ada OOM; proses tetap sehat |
| Soak | 5 pesan/menit, 5 chat | 24 jam | RSS < 300 MB dan pertumbuhan < 10% setelah 1 jam pertama, jumlah fd stabil, audit terotasi, 0 kebocoran thread |
| Burst per chat | 10 pesan berurutan di 1 chat | 1 menit | urutan terjaga, tidak ada riwayat tertimpa |
| Shutdown saat sibuk | 20 pekerjaan aktif, SIGTERM | — | shutdown <= grace + 2 s, audit mencatat jumlah yang dibatalkan |

Kriteria soak dinyatakan **lulus** hanya bila seluruh baris terpenuhi dan hasil mentah (CSV ringkas) disimpan di `docs/LOG.md` sebagai angka, bukan klaim.

### Failure injection

| Kegagalan | Cara memicu | Perilaku yang diharapkan |
|---|---|---|
| LLM 500 / timeout | MockTransport / cabut jaringan | retry plan-2, lalu balasan ramah; audit `error`; tidak ada crash |
| Bridge mati | `systemctl stop sanka-bridge` | `/readyz` 503 dalam 60 s, inbound berhenti, pesan keluar gagal tanpa crash, pulih otomatis |
| Disk penuh | `fallocate` di `/var/lib/sanka` | tool istimewa ditolak `audit_unavailable`; log error; `/readyz` 503; tidak ada data setengah tertulis |
| Config rusak | YAML tidak valid | `/readyz` 503, proses tetap hidup, pesan error menyebut key (tanpa nilai secret) |
| Secret 0644 | `chmod 644` | startup gagal `EX_NOPERM`, atau reload ditolak dan tetap memakai secret lama yang valid |
| SIGKILL saat kerja | `kill -9` | systemd restart, lock file dilepas, tidak ada balasan ganda, audit utuh sampai baris terakhir |
| Shell menyala | `shell_enabled: true` lalu HUP | `/readyz` menandai `shell=on` (bukan gagal), log level `warning` sekali per reload |
| Shell macet | `sleep 600` | mati setelah 30 s, `error_code=timeout`, audit `timeout` |
| Fork bomb | skrip rekursif | dibatasi `max_processes`, proses group dimatikan |
| Memori besar | alokasi 4 GB | `RLIMIT_AS` menolak, hasil `exit_nonzero`, host aman |

### Review keamanan dan rilis

- Checklist review keamanan (di `docs/SECURITY.md`, bagian baru) mencakup: empat lapis izin tool, toggle default `false`, tidak ada secret di log/audit/reply, permission file, sandbox rlimit, SSRF browser, path traversal `file_write`, HMAC audit, endpoint health loopback, unit systemd, backup terenkripsi, rotasi, dan bukti tes.
- `docs/RUNBOOK.md`: deteksi → containment → bukti → pemulihan → postmortem, dengan skenario: dugaan kebocoran API key, nomor diblokir, shell disalahgunakan, disk penuh, bridge logout, skill memori tercemar (plan-7), audit tidak bisa ditulis.
- `docs/RELEASE.md`: pra-rilis (tes, review, backup terverifikasi), rilis (rilis baru di `releases/<versi>`, `pip install`, symlink switch, `systemctl restart`, `/readyz`, smoke DM + grup), pasca-rilis (24 jam pantau), rollback (symlink ke rilis sebelumnya, restart, verifikasi; tidak ada migrasi data sehingga rollback aman).

## Dependency baru

Tidak ada paket Python baru. Semua memakai stdlib (`subprocess`, `resource`, `signal`, `socket` untuk sd_notify, `hmac`, `json`, `logging`, `tarfile`, `hashlib`), `httpx` dan `yaml` yang sudah ada, serta `pytest` untuk tes.

Kebutuhan sistem (bukan dependency kode, dicatat agar tidak mengejutkan):

- `systemd` >= 249 pada host target; `systemd-analyze` untuk verifikasi unit di CI/manual.
- `chromium`/`google-chrome` hanya bila `tools.browser_enabled: true`.
- `age` opsional untuk enkripsi backup; tanpa `age`, backup tetap berjalan tanpa enkripsi dan itu dicatat di log backup.
- `podman` atau `docker` hanya bila memilih jalur container.

ADR yang diajukan: ADR-0022 (sandbox proses), ADR-0023 (supervisor systemd utama, container alternatif), ADR-0024 (audit + redaksi log).

## Langkah

1. Ajukan ADR-0022/0023/0024 dan plan ini ke owner; ubah status menjadi `In Progress` setelah disetujui.
2. Perluas `sanka/config.py`: key `tools.code_run_enabled`, `tools.file_write_enabled`, `tools.file_write_root`, `tools.workspace_root`, `tools.browser_binary`, `tools.shell_timeout_seconds`, `tools.code_timeout_seconds`, `tools.max_tool_output_bytes`, `tools.max_write_bytes`, `limits.shutdown_grace_seconds`, `limits.max_active_chats`, `ops.health_bind`, `ops.health_port`, `ops.audit_dir`, `ops.log_level`. Semua default aman (`false`, kosong) dan validasi gagal cepat. Perbarui `config/sanka.example.yaml`.
3. Tambahkan `tests/test_config.py` untuk key baru dan nilai default; pastikan config lama yang belum punya key tetap dimuat dengan default aman (kompatibilitas ke belakang).
4. Implementasikan `sanka/logs.py` (`Redactor`, `configure_logging`, `log_event`) dan ganti seluruh `print`/`logging` ad-hoc di `sanka/` dengan `log_event`. Tambahkan `tests/test_redact.py`.
5. Implementasikan `sanka/audit.py` (backend JSONL, HMAC id, rotasi, fail-closed) dan sambungkan ke `sanka/tools/privileged.py`. Tambahkan `tests/test_audit.py`.
6. Implementasikan `sanka/tools/_limits.py` dan `sanka/tools/sandbox.py`; tes rlimit, timeout, kill group, batas output, dan pembersihan env.
7. Implementasikan `sanka/tools/privileged.py` (gate 1/2/3, validasi argumen, `ToolResult`) dan daftarkan ke registry plan-5. Tambahkan `tests/test_tools_permission.py` dan `tests/test_shell_toggle.py`.
8. Implementasikan `file_write` (path jail, symlink, atomic replace, pola terlarang) dan `browser_read` (profil khusus, SSRF, satu tembakan). Tambahkan `tests/test_file_write.py` dan `tests/test_browser_tool.py` dengan binary palsu.
9. Perluas `sanka/secrets.py` menjadi `SecretProvider` (fingerprint, reload mtime, key wajib) dan hubungkan SIGHUP. Tambahkan `tests/test_secrets_reload.py`.
10. Implementasikan `sanka/ops/lifecycle.py`, `notify.py`, `health.py`, `doctor.py`, `cli.py`; pasang `/healthz` dan `/readyz` di server plan-3. Tambahkan `tests/test_lifecycle.py`, `tests/test_health.py`, `tests/test_doctor.py`. Verifikasi `MemoryDenyWriteExecute` dan `RestrictNamespaces` terhadap Chromium serta library yang dipakai.
11. Tulis unit systemd (agent, bridge, backup + timer) dan jalankan `systemd-analyze verify`; uji manual restart, SIGTERM saat sibuk, watchdog (`kill -STOP` pada proses utama).
12. Tulis `deploy/install.sh` idempoten: buat user `sanka`, direktori + permission, venv, salin config contoh ke `/etc/sanka` bila belum ada, buat `SANKA_AUDIT_HMAC_KEY` bila belum ada, pasang unit, daemon-reload, enable. Uji di mesin bersih.
13. Tulis `deploy/backup.sh`, `restore.sh`, timer, dan lakukan satu uji restore penuh ke direktori sementara.
14. Tulis `deploy/Containerfile` dan `compose.yaml`; verifikasi `/healthz` dari host dan bahwa rootfs read-only.
15. Tulis `scripts/loadtest.py`, jalankan skenario beban; jalankan soak 24 jam di lingkungan staging (boleh di belakang jadwal terpisah) dan catat hasil.
16. Lakukan failure injection sesuai tabel; catat setiap hasil di `docs/LOG.md`.
17. Tulis `docs/RUNBOOK.md`, `docs/RELEASE.md`, dan bagian baru `docs/SECURITY.md` (sandbox, audit, rotasi, checklist review).
18. Lakukan review keamanan memakai checklist; perbaiki temuan tinggi/kritis.
19. Jalankan `pytest -q`, `python -m compileall sanka core gateway`, `systemd-analyze verify`, dan smoke nyata (DM + grup, satu `shell` dengan toggle menyala lalu dimatikan).
20. Isi "Hasil", ubah status plan menjadi `Done`, tambahkan entri `docs/LOG.md`, dan pastikan ADR-0022/0023/0024 `Accepted` atau dicatat sebagai `Proposed` bila owner belum memutuskan.

## Tes

| File/tes | Skenario |
|---|---|
| `test_config.py` (perluasan) | Key baru punya default aman (`false`/kosong); config lama tanpa key baru tetap termuat; `shell_enabled` tidak valid gagal cepat |
| `test_tools_permission.py` | Matriks 4 tool x 3 peran: owner diizinkan (toggle on), panitia/peserta selalu `denied`, termasuk saat toggle `true` |
| `test_tools_permission.py` | Skema tool tidak muncul untuk panitia/peserta; `tool_call` paksa tetap ditolak dan tercatat audit |
| `test_shell_toggle.py` | `shell_enabled: false` menolak owner; `true` mengizinkan owner; toggle berubah + SIGHUP berlaku pada panggilan berikutnya tanpa restart |
| `test_shell_toggle.py` | Deny menulis audit `shell`/`deny` dengan alasan `disabled` atau `role` |
| `test_sandbox.py` | `sleep 600` dimatikan sekitar timeout; anak proses ikut mati (process group) |
| `test_sandbox.py` | Output > batas dipotong dan ditandai `truncated`; exit code non-nol dilaporkan tanpa exception |
| `test_sandbox.py` | `env` tidak memuat secret; cwd berada di workspace; rlimit memori/CPU/fsize berlaku |
| `test_file_write.py` | `../`, path absolut, symlink keluar root, dan target `config/secrets.env` ditolak |
| `test_file_write.py` | Tulisan >256 KB ditolak; tulisan normal atomik (tidak ada file parsial saat simulasi gagal) |
| `test_browser_tool.py` | Toggle off → deny; binary tidak ada → `spawn_failed`; argumen profil khusus dipakai |
| `test_browser_tool.py` | URL `file://`, `localhost`, dan IP privat ditolak |
| `test_redact.py` | Nilai API key dan header `Bearer` hilang dari output log; nomor telepon tersamar; JSON tetap valid |
| `test_redact.py` | Field terlarang (`text`, `content`) dibuang; secret yang dirotasi tetap diredaksi sampai proses berhenti |
| `test_audit.py` | Id tersimpan sebagai HMAC 16 hex; berkas 0600, direktori 0700; rotasi bulanan membuat berkas baru |
| `test_audit.py` | Audit gagal tulis → tool istimewa ditolak `audit_unavailable`; aksi ditolak tetap tercatat |
| `test_secrets_reload.py` | File 0644 ditolak; env menang atas file; SIGHUP memuat nilai baru; fingerprint berubah tanpa membocorkan nilai |
| `test_health.py` | `/healthz` 200 saat normal; `/readyz` 503 saat bridge mati, secret hilang, atau disk < 500 MB |
| `test_health.py` | Body tidak memuat path absolut, secret, atau pesan error mentah; bind hanya loopback |
| `test_lifecycle.py` | SIGTERM: berhenti menerima, menunggu grace, membatalkan antrean, flush audit, exit 0 |
| `test_lifecycle.py` | SIGTERM kedua memaksa exit 1; lock file mencegah instance ganda |
| `deploy/systemd` (manual) | `kill -9` saat pekerjaan aktif → systemd restart, lock dilepas, tidak ada balasan ganda, audit utuh sampai baris terakhir |
| `test_doctor.py` | Doctor melaporkan config hilang, permission secret salah, direktori runtime tidak ada, dan toggle aktif; exit code sesuai |
| `deploy/systemd` (manual) | `systemd-analyze verify` lulus; `systemctl restart` pulih < 10 s; watchdog me-restart proses yang di-`STOP` |
| `deploy/restore.sh` (manual) | Restore dari backup terverifikasi ke direktori sementara menghasilkan isi identik (checksum) |
| `scripts/loadtest.py` | Kriteria beban, burst, shutdown-saat-sibuk, dan soak pada tabel "Uji beban dan soak" terpenuhi |

## Kriteria selesai

1. `pytest -q` lulus seluruh tes plan-1 sampai plan-8, dan `python -m compileall sanka core gateway` bersih.
2. Shell **nonaktif** secara default: `config/sanka.example.yaml` memuat `shell_enabled: false`, dan tes membuktikan owner pun ditolak saat toggle mati.
3. Panitia dan peserta tidak bisa mencapai `browser_read`, `code_run`, `file_write`, maupun `shell` lewat jalur mana pun (skema, registry, `tool_call` paksa, atau pemanggilan langsung `privileged.run`), dan setiap penolakan tercatat di audit.
4. Log dan audit tidak memuat isi pesan pribadi, nomor telepon mentah, API key, token, atau isi `secrets.env`; dibuktikan oleh `test_redact.py` dan pemeriksaan manual satu hari log staging.
5. Secret bisa dirotasi tanpa menghentikan layanan (SIGHUP) dan tanpa menyentuh git; `doctor` mendeteksi permission yang salah.
6. Layanan berjalan di bawah systemd, `Restart=on-failure` bekerja, watchdog me-restart proses yang macet, dan SIGTERM melakukan graceful shutdown tanpa balasan ganda.
7. Setelah host reboot, `sanka` dan `sanka-bridge` kembali hidup tanpa intervensi manual dan `/readyz` 200 dalam 60 detik (kecuali WhatsApp perlu pairing ulang — itu dicatat sebagai kejadian terpisah).
8. Backup harian berjalan, checksum terverifikasi, dan satu uji restore penuh berhasil.
9. Skrip beban dan soak memenuhi kriteria yang dinyatakan, dengan angka tercatat di `docs/LOG.md`.
10. Checklist review keamanan selesai; tidak ada temuan kategori tinggi/kritis yang terbuka (temuan sedang dicatat dengan pemilik dan tenggat).
11. Owner bisa mengulang deployment dari nol di mesin bersih hanya dengan `deploy/install.sh` + `docs/RELEASE.md`, dua kali berturut-turut, tanpa langkah lisan.
12. `docs/RUNBOOK.md` dan `docs/RELEASE.md` ada, dan `docs/SECURITY.md` sudah memuat bagian sandbox, audit, rotasi, dan checklist review.

## Yang perlu dipahami owner

- **Empat lapis izin, bukan satu.** Skema tidak dikirim ke LLM, registry menolak, `privileged.run()` memeriksa ulang peran, dan toggle config hanya bisa mengurangi. Kalau satu lapis dilewati karena bug, tiga lainnya menahan.
- **Shell adalah shell.** Owner yang bisa menjalankan shell berarti siapa pun yang memegang nomor owner bisa menjalankan perintah di host. Sandbox membatasi dampak (waktu, memori, proses), bukan mencegah pembacaan berkas yang bisa dibaca user `sanka`. Karena itu toggle default mati dan audit wajib.
- **Redaksi memakai dua cara.** Pertama, mencocokkan nilai secret yang benar-benar dimuat; kedua, regex sebagai jaring pengaman. Regex saja tidak cukup, exact match saja tidak menangkap token asing di pesan error.
- **Audit fail-closed.** Bila audit tidak bisa ditulis, tool istimewa berhenti. Ini disengaja: aksi tanpa jejak tidak boleh terjadi.
- **`/readyz` bukan `/healthz`.** `healthz` hanya "proses hidup"; `readyz` berarti "siap melayani", termasuk bridge dan secret. Bridge yang mati tidak boleh membuat proses Python crash-loop.
- **Sesi tetap hilang saat restart.** Plan-8 tidak mengubah ADR-0016. Yang dijaga adalah tidak ada balasan ganda, audit utuh, dan layanan pulih cepat.
- **Restart bukan rollback.** Rollback = menukar symlink `current` ke rilis sebelumnya. Tidak ada migrasi data, jadi rollback aman; yang hilang hanya sesi in-memory.
- **`deploy/install.sh` harus idempoten.** Menjalankannya dua kali tidak boleh merusak config, secret, atau audit yang sudah ada.

## Risiko

- **Shell dan `code_run` setara akses host untuk owner.** Isolasi sesungguhnya (VM/container terpisah untuk tool) di luar cakupan. Mitigasi: toggle default mati, peran owner, rlimit, timeout, audit, dan rekomendasi menjalankan Sanka di VPS khusus, bukan laptop kerja owner. Bila owner ingin isolasi lebih kuat, itu ADR/plan baru.
- **Kebocoran lewat pesan error library.** `httpx`/`yaml`/`subprocess` bisa memuat potongan input di exception. Mitigasi: `Redactor` pada handler, tes dengan payload mirip secret, dan larangan mengirim `str(exc)` ke pengguna.
- **Bridge Baileys tetap jalur tidak resmi.** Plan-8 tidak mengubah risiko blokir nomor; rate limit dan perilaku non-spam tetap mitigasi utama. Backup sesi WhatsApp membantu pairing ulang, tetapi tidak menghapus risiko.
- **Dedupe `message_id` masih in-memory.** Restart di tengah reconnect bisa memproses ulang pesan yang sama bila bridge mengirim ulang. Dampaknya balasan ganda, bukan kerusakan data. Bila ini terbukti terjadi di produksi, dedupe persisten menjadi plan terpisah.
- **Antrean keluar tidak persisten.** Balasan yang belum terkirim hilang saat crash. Alternatif (outbox SQLite) sengaja ditunda agar plan ini tidak melebar.
- **Chromium di bawah hardening systemd.** `NoNewPrivileges`/`RestrictNamespaces` dapat membuat sandbox Chromium gagal. Bila terukur gagal, opsi: unit `sanka-browser.service` terpisah tanpa dua directive itu, atau `browser_enabled` tetap mati. Keputusan diambil dari hasil langkah 10, dicatat di LOG; **tidak** memakai `--no-sandbox` sebagai jawaban default.
- **Soak 24 jam memakan waktu nyata.** Kriteria bisa terpenuhi lebih lambat dari tenggat plan; plan tidak dianggap selesai sebelum angka soak ada, atau owner menyetujui pengecualian tertulis.
- **Batas fase roadmap.** Plan-8 sengaja tidak menyerap fitur plan-6/7. Bila review menemukan kebutuhan fitur (misalnya outbox persisten atau browser interaktif), itu dicatat sebagai plan baru, bukan ditambahkan diam-diam di sini.
- **Ketergantungan pada nama modul plan-3/5/6/7.** Plan-8 mengasumsikan `sanka/tools/registry.py`, `sanka/audit.py`, server HTTP plan-3, dan provider secret plan-2. Bila nama atau signature berbeda saat implementasi, yang disesuaikan adalah plan-8 (penyesuaian kecil), bukan sebaliknya; perbedaan dicatat di "Catatan perubahan".

## Catatan perubahan

Belum ada.

## Hasil

Belum dikerjakan.
