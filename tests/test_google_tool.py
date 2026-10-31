"""Kontrak plugin Google: satu service account, identity-linked, permission per file."""

from __future__ import annotations

import asyncio

import pytest
import yaml
from support import build_config, database

from sanka.admin.templates import ROLES
from sanka.engine.roles import Role
from sanka.engine.schema import google_identity
from sanka.engine.tools import REGISTRY, ToolContext
from sanka.engine.tools import google as google_tool
from sanka.vendor.google_workspace.service import (
    DOC_MIME,
    SHEET_MIME,
    GoogleError,
    GoogleWorkspace,
)


class Request:
    def __init__(self, payload: dict | None = None) -> None:
        self.payload = payload or {}
        self.kwargs: dict = {}

    def execute(self) -> dict:
        return self.payload


class FakeDrive:
    def __init__(self, files: list[dict]) -> None:
        self.payload = {"files": files}

    def files(self):
        return self

    def list(self, **kwargs):
        return Request(self.payload)


def service(tmp_path) -> GoogleWorkspace:
    return GoogleWorkspace(database(tmp_path), env={})


def test_login_requires_a_direct_message(tmp_path) -> None:
    result = asyncio.run(service(tmp_path).login("p1", is_dm=False))

    assert result == "error: Google login is only allowed in a direct message."


def test_device_login_polls_until_verified(tmp_path, monkeypatch) -> None:
    svc = GoogleWorkspace(
        database(tmp_path),
        env={"GOOGLE_OAUTH_CLIENT_ID": "cid", "GOOGLE_OAUTH_CLIENT_SECRET": "sec"},
    )

    async def post(url, data, *, strict=True):
        if url == "https://oauth2.googleapis.com/device/code":
            return {
                "device_code": "dc",
                "user_code": "UC-123",
                "verification_url": "https://example.test/device",
                "expires_in": 1800,
                "interval": 1,
            }
        return {"access_token": "tok"}

    async def get(url, *, headers):
        assert headers["Authorization"] == "Bearer tok"
        return {"email": "Me@Example.com", "email_verified": True, "sub": "s1"}

    monkeypatch.setattr(svc, "_post", post)
    monkeypatch.setattr(svc, "_get", get)

    async def flow():
        text = await svc.login("p1", is_dm=True)
        for _ in range(40):
            if svc.identity("p1") is not None:
                return text
            await asyncio.sleep(0.1)
        return text

    text = asyncio.run(flow())

    assert "UC-123" in text and "https://example.test/device" in text
    assert svc.identity("p1")["email"] == "me@example.com"
    assert svc._device("p1") is None


def test_status_without_identity_is_not_connected(tmp_path) -> None:
    result = asyncio.run(service(tmp_path).status("p1", is_dm=True))

    assert result == "not connected; use action=login"


def test_missing_service_account_is_a_safe_error(tmp_path) -> None:
    svc = service(tmp_path)
    with pytest.raises(GoogleError, match="GOOGLE_SERVICE_ACCOUNT_PATH is not set"):
        asyncio.run(svc.list_files("me@example.com", False, "", 5))


def test_role_for_filters_by_explicit_email(tmp_path) -> None:
    svc = service(tmp_path)
    shared = {
        "permissions": [
            {"type": "user", "role": "reader", "emailAddress": "Me@Example.com"},
            {"type": "user", "role": "writer", "emailAddress": "boss@example.com"},
        ]
    }

    assert svc._role_for(shared, "me@example.com", False, False) == "reader"
    with pytest.raises(GoogleError, match="not shared"):
        svc._role_for(shared, "other@example.com", False, False)
    with pytest.raises(GoogleError, match="read-only"):
        svc._role_for(shared, "me@example.com", False, True)
    assert svc._role_for(shared, "boss@example.com", False, True) == "writer"


def test_link_sharing_is_rejected_even_for_trusted(tmp_path) -> None:
    svc = service(tmp_path)
    link = {"permissions": [{"type": "anyone", "role": "reader"}]}
    domain = {"permissions": [{"type": "domain", "role": "writer"}]}

    with pytest.raises(GoogleError, match="link-sharing"):
        svc._role_for(link, "me@example.com", True, False)
    with pytest.raises(GoogleError, match="link-sharing"):
        svc._role_for(domain, "me@example.com", False, False)


def test_trusted_bypasses_email_but_keeps_the_file_shared(tmp_path) -> None:
    svc = service(tmp_path)
    file = {"permissions": [{"type": "user", "role": "reader", "emailAddress": "x@y.z"}]}

    assert svc._role_for(file, "someone@else.com", True, True) == "owner"


def test_list_files_only_returns_explicitly_shared_files(tmp_path) -> None:
    svc = service(tmp_path)
    svc._drive = FakeDrive(
        [
            {
                "id": "1",
                "name": "Shared",
                "mimeType": SHEET_MIME,
                "permissions": [
                    {"type": "user", "role": "reader", "emailAddress": "me@example.com"}
                ],
            },
            {
                "id": "2",
                "name": "Other",
                "mimeType": SHEET_MIME,
                "permissions": [
                    {"type": "user", "role": "writer", "emailAddress": "other@example.com"}
                ],
            },
            {
                "id": "3",
                "name": "Link",
                "mimeType": SHEET_MIME,
                "permissions": [
                    {"type": "anyone", "role": "reader"},
                    {"type": "user", "role": "writer", "emailAddress": "me@example.com"},
                ],
            },
            {
                "id": "4",
                "name": "GroupOnly",
                "mimeType": SHEET_MIME,
                "permissions": [{"type": "group", "role": "writer", "emailAddress": "g@x.y"}],
            },
        ]
    )

    out = asyncio.run(svc.list_files("me@example.com", False, "", 10))

    assert out == f"1\tShared\t{SHEET_MIME}\treader"


def test_write_updates_a_sheet_after_the_permission_check(tmp_path) -> None:
    svc = service(tmp_path)
    seen: dict = {}

    async def authorize(file_id, email, trusted, write):
        seen.update(file_id=file_id, email=email, trusted=trusted, write=write)
        return {"id": file_id, "name": "Sheet", "mimeType": SHEET_MIME}

    class Values:
        def update(self, **kwargs):
            seen.update(kwargs)
            return Request()

    class Spreadsheets:
        def values(self):
            return Values()

    class Sheets:
        def spreadsheets(self):
            return Spreadsheets()

    svc._authorize = authorize
    svc._sheets = Sheets()

    out = asyncio.run(svc.write("s1", "me@example.com", False, "A1", [["a", "b"]]))

    assert out == "ok: wrote 2 cells to Sheet"
    assert seen["write"] is True
    assert seen["body"]["values"] == [["a", "b"]]
    assert seen["valueInputOption"] == "USER_ENTERED"


def test_docs_read_is_supported_and_other_types_are_not(tmp_path) -> None:
    svc = service(tmp_path)

    async def authorize(file_id, email, trusted, write):
        return {"id": file_id, "name": "Doc", "mimeType": DOC_MIME}

    svc._authorize = authorize

    class Documents:
        def get(self, **kwargs):
            return Request(
                {
                    "body": {
                        "content": [
                            {"paragraph": {"elements": [{"textRun": {"content": "isi doc"}}]}}
                        ]
                    }
                }
            )

    class Docs:
        def documents(self):
            return Documents()

    svc._docs = Docs()
    assert asyncio.run(svc.read("d1", "me@example.com", False, "")) == "isi doc"

    async def authorize_other(file_id, email, trusted, write):
        return {"id": file_id, "name": "Bin", "mimeType": "application/pdf"}

    svc._authorize = authorize_other
    assert (
        asyncio.run(svc.read("d2", "me@example.com", False, ""))
        == "error: only Google Docs and Sheets are supported"
    )


def test_tool_discovery_and_role_grants() -> None:
    handler = REGISTRY.get("google")

    assert handler is not None and handler.name == "google" and handler.family == "mcp"
    assert REGISTRY.get("gworkspace") is handler

    assert "google" in ROLES["member"]["tools"]
    assert "mcp__*" not in ROLES["member"]["tools"]
    assert "google" not in ROLES["guest"]["tools"]

    live = yaml.safe_load(
        (__import__("pathlib").Path(__file__).resolve().parents[1] / "roles.yaml").read_text(
            encoding="utf-8"
        )
    )["roles"]
    assert "google" in live["member"]["tools"]
    assert "mcp__*" not in live["member"]["tools"]


def test_tool_blocks_actions_without_an_identity(tmp_path, monkeypatch) -> None:
    class Fake:
        def identity(self, person_id):
            return None

    monkeypatch.setattr(google_tool, "_service", lambda ctx: Fake())
    config = build_config(
        tmp_path, roles={"member": Role("member", tools=("google",))}
    )
    ctx = ToolContext(
        config,
        config.roles.default_role(),
        ("google",),
        chat_id="chat",
        sender="p1",
        person_id="p1",
    )

    for action in ("list", "read", "write"):
        result = asyncio.run(google_tool._google({"action": action}, ctx))
        assert result == "error: Google account not connected; run action=login in a DM"


def test_tool_login_in_group_is_rejected(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(google_tool, "_service", lambda ctx: service(tmp_path))
    config = build_config(
        tmp_path, roles={"member": Role("member", tools=("google",))}
    )
    ctx = ToolContext(
        config,
        config.roles.default_role(),
        ("google",),
        chat_id="chat",
        sender="p1",
        person_id="p1",
        is_dm=False,
    )

    result = asyncio.run(google_tool._google({"action": "login"}, ctx))

    assert result == "error: Google login is only allowed in a direct message."


def test_tool_routes_actions_with_a_linked_identity(tmp_path, monkeypatch) -> None:
    calls: list[tuple] = []

    class Fake:
        def identity(self, person_id):
            return {"email": "me@example.com"}

        async def list_files(self, email, trusted, query, limit):
            calls.append(("list", email, trusted, query, limit))
            return "listed"

        async def read(self, file_id, email, trusted, range_value):
            calls.append(("read", file_id, email, trusted, range_value))
            return "read"

        async def write(self, file_id, email, trusted, range_value, values):
            calls.append(("write", file_id, email, trusted, range_value, values))
            return "written"

    monkeypatch.setattr(google_tool, "_service", lambda ctx: Fake())
    config = build_config(
        tmp_path, roles={"member": Role("member", tools=("google",))}
    )
    ctx = ToolContext(
        config,
        config.roles.default_role(),
        ("google",),
        chat_id="chat",
        sender="p1",
        person_id="p1",
    )

    assert asyncio.run(
        google_tool._google({"action": "list", "query": "q", "limit": 3}, ctx)
    ) == "listed"
    assert asyncio.run(
        google_tool._google({"action": "read", "file_id": "f1", "range": "A1:B2"}, ctx)
    ) == "read"
    assert asyncio.run(
        google_tool._google(
            {"action": "write", "file_id": "f1", "range": "A1", "values": [["x"]]}, ctx
        )
    ) == "written"
    assert calls == [
        ("list", "me@example.com", False, "q", 3),
        ("read", "f1", "me@example.com", False, "A1:B2"),
        ("write", "f1", "me@example.com", False, "A1", [["x"]]),
    ]


def test_identity_row_is_read_by_person(tmp_path) -> None:
    db = database(tmp_path)
    with db.connect() as conn:
        db.upsert(
            conn,
            google_identity,
            [{"person_id": "p1", "email": "me@example.com", "subject": "s", "linked_at": 1.0}],
            ["person_id"],
        )

    svc = GoogleWorkspace(db, env={})

    assert svc.identity("p1")["email"] == "me@example.com"
    assert svc.identity("p2") is None
