"""Google Workspace in-process: one service account, identity-linked per sender.

The service account can see every file shared with it. A human's verified Google email
(device flow, `openid email profile`) is checked against the file's explicit user
permission before any read/write; link-sharing (`anyone`/`domain`) is rejected outright.
No token or email is ever logged.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

import httpx
from sqlalchemy import delete, select

from ...engine.schema import google_device, google_identity

log = logging.getLogger(__name__)

DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
TOKEN_URL = "https://oauth2.googleapis.com/token"
USERINFO_URL = "https://openidconnect.googleapis.com/v1/userinfo"
SCOPES = (
    "https://www.googleapis.com/auth/drive",
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/documents",
)
DRIVE_FIELDS = "id,name,mimeType,permissions(type,role,emailAddress)"
SHEET_MIME = "application/vnd.google-apps.spreadsheet"
DOC_MIME = "application/vnd.google-apps.document"
CALL_TIMEOUT = 30
READ_LIMIT = 20_000


class GoogleError(RuntimeError):
    """Safe, user-facing Google failure; the message carries no secrets."""


class GoogleWorkspace:
    def __init__(self, db, env=os.environ):
        self.db = db
        self.env = env
        self._credentials = None
        self._drive = None
        self._docs = None
        self._sheets = None
        self._tasks: set[asyncio.Task] = set()

    # ---------------------------------------------------------- credentials

    def _creds(self):
        if self._credentials is not None:
            return self._credentials
        path = str(self.env.get("GOOGLE_SERVICE_ACCOUNT_PATH") or "").strip()
        if not path:
            raise GoogleError("GOOGLE_SERVICE_ACCOUNT_PATH is not set")
        if not Path(path).is_file():
            raise GoogleError("service account file not found")
        from google.oauth2 import service_account

        self._credentials = service_account.Credentials.from_service_account_file(
            path, scopes=SCOPES
        )
        return self._credentials

    def _client(self, api: str):
        cached = getattr(self, f"_{api}", None)
        if cached is not None:
            return cached
        credentials = self._creds()
        from googleapiclient.discovery import build

        versions = {"drive": "v3", "docs": "v1", "sheets": "v4"}
        client = build(api, versions[api], credentials=credentials, cache_discovery=False)
        setattr(self, f"_{api}", client)
        return client

    # ---------------------------------------------------------- device flow

    async def login(self, person_id: str, *, is_dm: bool) -> str:
        if not is_dm:
            return "error: Google login is only allowed in a direct message."
        known = self.identity(person_id)
        if known is not None:
            return f"already connected as {known['email']}"
        client_id = str(self.env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        secret = str(self.env.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
        if not client_id or not secret:
            raise GoogleError("GOOGLE_OAUTH_CLIENT_ID/GOOGLE_OAUTH_CLIENT_SECRET is not set")
        payload = await self._post(
            DEVICE_CODE_URL, {"client_id": client_id, "scope": "openid email profile"}
        )
        device_code = str(payload.get("device_code") or "")
        user_code = str(payload.get("user_code") or "")
        verification_url = str(payload.get("verification_url") or "")
        expires_in = int(payload.get("expires_in") or 1800)
        interval = max(1, int(payload.get("interval") or 5))
        if not device_code or not user_code or not verification_url:
            raise GoogleError("device authorization response is incomplete")
        self._write_device(
            person_id,
            device_code=device_code,
            user_code=user_code,
            verification_url=verification_url,
            expires_at=time.time() + expires_in,
            interval=interval,
            status="pending",
            last_error="",
            created_at=time.time(),
        )
        self._start_poll(person_id)
        log.info("google login person=%s outcome=pending", person_id)
        return (
            f"Open {verification_url} and enter code {user_code}. "
            "The code expires in 30 minutes."
        )

    async def status(self, person_id: str, *, is_dm: bool) -> str:
        known = self.identity(person_id)
        if known is not None:
            return f"connected as {known['email']}"
        row = self._device(person_id)
        if (
            row is not None
            and row["status"] == "pending"
            and float(row["expires_at"]) > time.time()
        ):
            self._start_poll(person_id)
            if is_dm:
                return (
                    f"pending; code {row['user_code']} at {row['verification_url']}"
                )
            return "pending"
        return "not connected; use action=login"

    def _start_poll(self, person_id: str) -> None:
        name = f"google-poll:{person_id}"
        for task in self._tasks:
            if task.get_name() == name and not task.done():
                return
        task = asyncio.create_task(self._poll_device(person_id), name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _poll_device(self, person_id: str) -> None:
        row = self._device(person_id)
        if row is None:
            return
        device_code = row["device_code"]
        interval = max(1, int(row["interval"] or 5))
        client_id = str(self.env.get("GOOGLE_OAUTH_CLIENT_ID") or "").strip()
        secret = str(self.env.get("GOOGLE_OAUTH_CLIENT_SECRET") or "").strip()
        while True:
            current = self._device(person_id)
            if current is None or current["device_code"] != device_code:
                return
            if float(current["expires_at"]) <= time.time():
                self._update_device(person_id, status="expired")
                log.info("google login person=%s outcome=expired", person_id)
                return
            await asyncio.sleep(interval)
            try:
                payload = await self._post(
                    TOKEN_URL,
                    {
                        "client_id": client_id,
                        "client_secret": secret,
                        "device_code": device_code,
                        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
                    },
                    strict=False,
                )
            except Exception as exc:  # jaringan putus: tetap pending sampai kedaluwarsa
                self._update_device(
                    person_id, last_error=f"{type(exc).__name__}: {exc}"[:300]
                )
                continue
            error = str(payload.get("error") or "")
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                interval += 5
                self._update_device(person_id, interval=interval)
                continue
            if error in {"access_denied", "expired_token"}:
                self._update_device(person_id, status=error)
                log.info("google login person=%s outcome=%s", person_id, error)
                return
            token = str(payload.get("access_token") or "")
            if not token:
                self._update_device(
                    person_id, last_error=str(error or "no access_token")[:300]
                )
                continue
            try:
                info = await self._get(
                    USERINFO_URL, headers={"Authorization": f"Bearer {token}"}
                )
            except Exception as exc:
                self._update_device(
                    person_id, last_error=f"{type(exc).__name__}: {exc}"[:300]
                )
                continue
            email = str(info.get("email") or "").strip().lower()
            if info.get("email_verified") is not True or not email:
                self._update_device(person_id, status="failed", last_error="email unverified")
                log.info("google login person=%s outcome=unverified", person_id)
                return
            self._write_identity(
                person_id,
                email=email,
                subject=str(info.get("sub") or ""),
                linked_at=time.time(),
            )
            with self.db.connect() as conn:
                conn.execute(delete(google_device).where(google_device.c.person_id == person_id))
            log.info("google login person=%s outcome=connected", person_id)
            return

    # ---------------------------------------------------------- state

    def identity(self, person_id: str) -> dict | None:
        return self.db.fetch_one(
            select(google_identity).where(google_identity.c.person_id == person_id)
        )

    def _device(self, person_id: str) -> dict | None:
        return self.db.fetch_one(
            select(google_device).where(google_device.c.person_id == person_id)
        )

    def _write_device(self, person_id: str, **values) -> None:
        row = {"person_id": person_id, **values}
        with self.db.connect() as conn:
            self.db.upsert(conn, google_device, [row], ["person_id"])

    def _update_device(self, person_id: str, **changes) -> None:
        row = self._device(person_id)
        if row is None:
            return
        row.update(changes)
        with self.db.connect() as conn:
            self.db.upsert(conn, google_device, [row], ["person_id"])

    def _write_identity(self, person_id: str, **values) -> None:
        row = {"person_id": person_id, **values}
        with self.db.connect() as conn:
            self.db.upsert(conn, google_identity, [row], ["person_id"])

    # ---------------------------------------------------------- files

    async def list_files(self, email: str, trusted: bool, query: str, limit: int) -> str:
        drive = self._client("drive")
        limit = max(1, min(int(limit or 10), 50))
        q = "trashed=false"
        needle = str(query or "").strip()
        if needle:
            escaped = needle.replace("\\", "\\\\").replace("'", "\\'")
            q = f"name contains '{escaped}' and trashed=false"
        payload = await self._call(
            lambda: drive.files()
            .list(
                q=q,
                pageSize=min(limit * 2, 100),
                fields=f"files({DRIVE_FIELDS})",
                supportsAllDrives=True,
                includeItemsFromAllDrives=True,
            )
            .execute()
        )
        lines: list[str] = []
        for file in payload.get("files", []) or []:
            if len(lines) >= limit:
                break
            try:
                if not file.get("permissions"):
                    file = await self._authorize(file["id"], email, trusted, write=False)
                role = self._role_for(file, email, trusted, write=False)
            except GoogleError:
                continue
            lines.append(
                "\t".join(
                    [
                        str(file.get("id") or ""),
                        str(file.get("name") or ""),
                        str(file.get("mimeType") or ""),
                        role,
                    ]
                )
            )
        return "\n".join(lines) or "No files shared with Sanka and your Google account."

    async def read(self, file_id: str, email: str, trusted: bool, range_value: str) -> str:
        file = await self._authorize(file_id, email, trusted, write=False)
        mime = str(file.get("mimeType") or "")
        if mime == SHEET_MIME:
            sheets = self._client("sheets")
            target = str(range_value or "A1:Z1000").strip() or "A1:Z1000"
            payload = await self._call(
                lambda: sheets.spreadsheets()
                .values()
                .get(spreadsheetId=file_id, range=target, majorDimension="ROWS")
                .execute()
            )
            text = "\n".join(
                "\t".join("" if cell is None else str(cell) for cell in row)
                for row in payload.get("values", []) or []
            )
            return text[:READ_LIMIT] if text else "(empty)"
        if mime == DOC_MIME:
            docs = self._client("docs")
            document = await self._call(
                lambda: docs.documents().get(documentId=file_id).execute()
            )
            chunks: list[str] = []
            for block in (document.get("body") or {}).get("content", []) or []:
                for element in (block.get("paragraph") or {}).get("elements", []) or []:
                    content = (element.get("textRun") or {}).get("content")
                    if content:
                        chunks.append(str(content))
            text = "".join(chunks)
            return text[:READ_LIMIT] if text else "(empty)"
        return "error: only Google Docs and Sheets are supported"

    async def write(
        self,
        file_id: str,
        email: str,
        trusted: bool,
        range_value: str,
        values: list[list],
    ) -> str:
        file = await self._authorize(file_id, email, trusted, write=True)
        if str(file.get("mimeType") or "") != SHEET_MIME:
            raise GoogleError("only Google Sheets can be written")
        target = str(range_value or "").strip()
        if not target:
            raise GoogleError("range is required for a spreadsheet write")
        if (
            not isinstance(values, list)
            or not values
            or not all(isinstance(row, list) for row in values)
        ):
            raise GoogleError("values must be a non-empty list of rows")
        sheets = self._client("sheets")
        await self._call(
            lambda: sheets.spreadsheets()
            .values()
            .update(
                spreadsheetId=file_id,
                range=target,
                valueInputOption="USER_ENTERED",
                body={"values": values},
            )
            .execute()
        )
        cells = sum(len(row) for row in values)
        name = str(file.get("name") or file_id)
        return f"ok: wrote {cells} cells to {name}"

    def _role_for(self, file: dict, email: str, trusted: bool, write: bool) -> str:
        permissions = file.get("permissions") or []
        if any(item.get("type") in {"anyone", "domain"} for item in permissions):
            raise GoogleError(
                "link-sharing is not allowed; share explicitly with your email and Sanka"
            )
        if trusted:
            return "owner"
        wanted = str(email or "").casefold()
        found = next(
            (
                item
                for item in permissions
                if item.get("type") == "user"
                and str(item.get("emailAddress") or "").casefold() == wanted
            ),
            None,
        )
        if found is None:
            raise GoogleError("your Google account is not shared on this file")
        role = str(found.get("role") or "")
        if write and role not in {"writer", "owner"}:
            raise GoogleError("you have read-only access to this file")
        return role

    async def _authorize(
        self, file_id: str, email: str, trusted: bool, write: bool
    ) -> dict:
        drive = self._client("drive")

        def fetch() -> dict:
            from googleapiclient.errors import HttpError

            try:
                return (
                    drive.files()
                    .get(fileId=file_id, fields=DRIVE_FIELDS, supportsAllDrives=True)
                    .execute()
                )
            except HttpError as exc:
                raise GoogleError(
                    "file is not shared with Sanka's service account"
                ) from exc

        file = await self._call(fetch)
        self._role_for(file, email, trusted, write)
        return file

    # ---------------------------------------------------------- http

    async def _call(self, fn):
        try:
            return await asyncio.wait_for(asyncio.to_thread(fn), CALL_TIMEOUT)
        except TimeoutError as exc:
            raise GoogleError("Google API timeout") from exc

    async def _post(self, url: str, data: dict, *, strict: bool = True) -> dict:
        async def send() -> dict:
            async with httpx.AsyncClient(timeout=CALL_TIMEOUT) as client:
                response = await client.post(url, data=data)
                if strict:
                    response.raise_for_status()
                return response.json()

        try:
            return await asyncio.wait_for(send(), CALL_TIMEOUT)
        except TimeoutError as exc:
            raise GoogleError("Google API timeout") from exc

    async def _get(self, url: str, *, headers: dict) -> dict:
        async def send() -> dict:
            async with httpx.AsyncClient(timeout=CALL_TIMEOUT) as client:
                response = await client.get(url, headers=headers)
                response.raise_for_status()
                return response.json()

        try:
            return await asyncio.wait_for(send(), CALL_TIMEOUT)
        except TimeoutError as exc:
            raise GoogleError("Google API timeout") from exc
