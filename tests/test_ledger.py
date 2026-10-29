"""Kontrak ledger aksi: autografis, terbatas, dan menyaring rahasia."""

from __future__ import annotations

from sanka.engine.memory.ledger import ActionLedger
from support import database


def test_log_and_recent_order(tmp_path) -> None:
    ledger = ActionLedger(database(tmp_path))
    first = ledger.log_action("whatsapp", "chat-b", "send", "kirim laporan")
    second = ledger.log_action(
        "whatsapp",
        "chat-c",
        "send",
        "kirim foto",
        source_name="Grup A",
        target_name="Budi",
        request="tolong kirim",
    )

    assert first < second
    rows = ledger.recent_actions()
    assert [row["kind"] for row in rows] == ["send", "send"]
    assert rows[0]["target_name"] == "Budi"
    assert ledger.count() == 2


def test_actions_context_headers_and_request(tmp_path) -> None:
    ledger = ActionLedger(database(tmp_path))
    assert ledger.actions_context() == ""
    ledger.log_action("whatsapp", "chat", "send", "kirim stiker", request="kirim stiker dong")

    text = ledger.actions_context()
    assert text.startswith("Recent cross-chat actions")
    assert "whatsapp -> chat" in text
    assert 'request: "kirim stiker dong"' in text


def test_sensitive_payload_never_stored(tmp_path) -> None:
    db = database(tmp_path)
    ledger = ActionLedger(db)
    ledger.log_action(
        "whatsapp",
        "chat",
        "send",
        "kirim password",
        request="api_key=rahasia",
    )
    rows = ledger.recent_actions()
    assert rows[0]["request"] == ""
    assert "[isi sensitif tidak dicatat]" in rows[0]["summary"]
    assert "rahasia" not in ledger.actions_context()
