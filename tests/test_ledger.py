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


def test_actions_context_is_internal_and_hides_the_requester(tmp_path) -> None:
    ledger = ActionLedger(database(tmp_path))
    assert ledger.actions_context() == ""
    ledger.log_action(
        "whatsapp",
        "chat",
        "teks",
        "kirim stiker",
        source_name="Grup A",
        request="kirim stiker dong",
    )

    text = ledger.actions_context()
    lines = text.splitlines()

    assert lines[0].startswith("## Recent actions")
    assert "never reveal who requested them" in lines[0]
    assert lines[1].startswith("- [") and lines[1].endswith("] teks -> chat: kirim stiker")
    assert "Grup A" not in text
    assert "kirim stiker dong" not in text


def test_actions_context_caps_lines_and_keeps_failures(tmp_path) -> None:
    ledger = ActionLedger(database(tmp_path))
    for index in range(15):
        ledger.log_action("whatsapp", f"chat-{index}", "teks", f"aksi {index}")
    ledger.log_action("whatsapp", "chat-x", "file", "gagal — error: gateway tidak tersedia")

    lines = ledger.actions_context().splitlines()

    assert len(lines) == 11  # header + maksimal 10 aksi
    assert lines[1].startswith("- [")
    assert "chat-x" in lines[1] and "gagal — error" in lines[1]
    assert "chat-0" not in ledger.actions_context()


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
