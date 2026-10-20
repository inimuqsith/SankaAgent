"""Native Sanka tool bundle.

Registers the plan-5 read tools with explicit input/output schemas. The same
``ToolRegistry`` gates every call through :mod:`sanka.policy`, so a role that
cannot see a tool cannot execute it either.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from sanka.attachments import AttachmentStore
from sanka.config import SankaConfig
from sanka.google import TokenProvider, build_token_provider
from sanka.tools.attachments import make_attachment_read_tool
from sanka.tools.docs import make_doc_read_tool
from sanka.tools.recap import RecapService, make_recap_tool
from sanka.tools.registry import ToolError, ToolRegistry, ToolSpec
from sanka.tools.sheets import (
    GoogleSheetsApi,
    SheetsApi,
    make_sheet_read_tool,
)
from sanka.tools.web import (
    SearchProvider,
    SearxngSearchProvider,
    make_web_search_tool,
)

_NOT_CONFIGURED_MESSAGE = "Akses sheet belum diatur."


class _UnavailableSheetsApi:
    """Fail every sheet call with ``not_configured`` until credentials exist.

    This keeps startup and the other tools working when the service-account
    env var is absent; the plan requires that behaviour.
    """

    def fetch(
        self, alias: str, cell_range: str, *, max_rows: int | None = None
    ) -> Any:
        del alias, cell_range, max_rows
        raise ToolError("not_configured", _NOT_CONFIGURED_MESSAGE)


def _default_sheets(
    config: SankaConfig, token_provider: TokenProvider | None
) -> SheetsApi:
    if token_provider is None:
        try:
            token_provider = build_token_provider(config)
        except ToolError:
            return _UnavailableSheetsApi()
    return GoogleSheetsApi(config, token_provider)


def build_tools(
    config: SankaConfig,
    *,
    now: Callable[[], datetime] | None = None,
    token_provider: TokenProvider | None = None,
    sheets: SheetsApi | None = None,
    search: SearchProvider | None = None,
    attachments: AttachmentStore | None = None,
) -> tuple[ToolSpec, ...]:
    """Build the read tool specs, including inbound attachments.

    Providers are injectable so tests never touch Google or SearXNG. Without
    credentials the sheet tools still register and answer ``not_configured``;
    the agent never sees a fabricated handler.
    """

    clock = now or (lambda: datetime.now(timezone.utc))
    active_search = search or SearxngSearchProvider(config.tools.web_search)
    active_sheets = sheets or _default_sheets(config, token_provider)
    recap_service = RecapService(config, active_sheets, clock)
    return (
        make_web_search_tool(config, active_search),
        make_doc_read_tool(config),
        make_attachment_read_tool(attachments),
        make_sheet_read_tool(active_sheets, config),
        make_recap_tool(recap_service),
    )


def build_registry(
    config: SankaConfig,
    *,
    now: Callable[[], datetime] | None = None,
    token_provider: TokenProvider | None = None,
    sheets: SheetsApi | None = None,
    search: SearchProvider | None = None,
    attachments: AttachmentStore | None = None,
) -> ToolRegistry:
    """Build a registry containing every read tool, including ``attachment_read``."""

    registry = ToolRegistry(config)
    for spec in build_tools(
        config,
        now=now,
        token_provider=token_provider,
        sheets=sheets,
        search=search,
        attachments=attachments,
    ):
        registry.register(spec)
    return registry


def build_recap_service(
    config: SankaConfig,
    *,
    now: Callable[[], datetime] | None = None,
    token_provider: TokenProvider | None = None,
    sheets: SheetsApi | None = None,
) -> RecapService:
    """Build the deterministic recap service used by ``/rekap`` and the tool."""

    clock = now or (lambda: datetime.now(timezone.utc))
    active_sheets = sheets or _default_sheets(config, token_provider)
    return RecapService(config, active_sheets, clock)
