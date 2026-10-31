"""Avatar profil WhatsApp: sinyal cadangan untuk gender, bukan sinyal utama.

Hanya dipakai ketika `engine.character.gender_avatar` menyala dan gaya mengetik tidak
memberi jawaban. Satu unduhan per orang per 30 hari, maksimal 2 MB, disimpan di
`workspace/avatars/` dengan izin 0600. Semua kegagalan berarti "tidak tahu".
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import time
import urllib.error
import urllib.request
from pathlib import Path

from neonize.utils.jid import build_jid

from ...engine.config import Config

log = logging.getLogger(__name__)

AVATAR_TTL = 30 * 24 * 3600
AVATAR_MAX_BYTES = 2_000_000
AVATAR_TIMEOUT = 10.0


def avatar_path(config: Config, jid: str) -> Path:
    digest = hashlib.sha256(str(jid or "").encode("utf-8")).hexdigest()[:24]
    return Path(config.workspace) / "avatars" / f"{digest}.bin"


def sniff_mime(raw: bytes) -> str:
    """Tebak tipe gambar dari magic bytes; selain gambar dianggap tidak dikenal."""
    if raw.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if raw.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if raw[:6] in (b"GIF87a", b"GIF89a"):
        return "image/gif"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    return ""


def _fresh(path: Path, now: float) -> bool:
    try:
        return path.is_file() and path.stat().st_size > 0 and now - path.stat().st_mtime < AVATAR_TTL
    except OSError:
        return False


def _download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "sanka-agent/0.1"})
    with urllib.request.urlopen(request, timeout=AVATAR_TIMEOUT) as response:
        return response.read(AVATAR_MAX_BYTES + 1)


async def avatar_bytes(client, config: Config, jid: str) -> tuple[bytes, str] | None:
    """(bytes gambar, mime) atau None; tidak pernah melempar ke pemanggil."""
    if client is None or not str(jid or "").strip():
        return None
    target = avatar_path(config, jid)
    if _fresh(target, time.time()):
        try:
            raw = target.read_bytes()
        except OSError:
            raw = b""
        mime = sniff_mime(raw)
        if raw and mime:
            return (raw, mime)
    try:
        user, _, server = str(jid).partition("@")
        info = await client.get_profile_picture(build_jid(user, server or "s.whatsapp.net"))
    except Exception:
        log.debug("avatar: get_profile_picture gagal untuk %s", jid[-6:], exc_info=True)
        return None
    url = str(getattr(info, "URL", "") or "")
    if not url:
        return None
    try:
        raw = await asyncio.to_thread(_download, url)
    except (OSError, urllib.error.URLError, TimeoutError):
        log.debug("avatar: unduhan gagal untuk %s", jid[-6:], exc_info=True)
        return None
    mime = sniff_mime(raw)
    if not raw or len(raw) > AVATAR_MAX_BYTES or not mime:
        return None
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(raw)
        target.chmod(0o600)
    except OSError:
        log.debug("avatar: gagal menyimpan cache", exc_info=True)
    return (raw, mime)
