"""Formatter tests: strip internal blocks, truncate, split recaps."""

from __future__ import annotations

from sanka.formatter import (
    Formatter,
    ResponseKind,
    strip_internal_blocks,
    to_whatsapp,
)


def test_strips_reasoning_thought_and_tool_call_blocks() -> None:
    raw = (
        "<reasoning>rahasia</reasoning>"
        "Jawaban final.\n"
        "<thought>pikiranku</thought>\n"
        "<tool_call>{'name': 'x'}</tool_call>"
    )

    assert strip_internal_blocks(raw).strip() == "Jawaban final."


def test_strip_is_case_insensitive_and_multiline() -> None:
    raw = "<REASONING>\nline one\nline two\n</REASONING>Halo."

    assert strip_internal_blocks(raw) == "Halo."


def test_strip_removes_self_closing_tool_calls() -> None:
    assert strip_internal_blocks("Halo <tool_call id='1'/> dunia") == "Halo  dunia"


def test_plain_text_is_untouched(config) -> None:
    formatter = Formatter(max_chars=100)

    assert formatter.format("Halo!") == ["Halo!"]


def test_normal_response_is_truncated(config) -> None:
    formatter = Formatter(max_chars=20)

    messages = formatter.format("x" * 50)

    assert len(messages) == 1
    assert len(messages[0]) <= 20
    assert messages[0].endswith("\u2026")


def test_recap_is_split_at_paragraph_boundaries() -> None:
    formatter = Formatter(max_chars=20)
    raw = "Paragraf satu.\n\nParagraf dua.\n\nParagraf tiga."

    messages = formatter.format(raw, ResponseKind.RECAP)

    assert len(messages) == 3
    assert messages == ["Paragraf satu.", "Paragraf dua.", "Paragraf tiga."]


def test_recap_packs_paragraphs_and_hard_wraps_long_ones() -> None:
    formatter = Formatter(max_chars=20)
    raw = "pendek\n\n" + "kata " * 10

    messages = formatter.format(raw, ResponseKind.RECAP)

    assert len(messages) > 1
    assert all(len(message) <= 20 for message in messages)
    assert messages[0] == "pendek"
    assert "kata" in " ".join(messages)


def test_empty_after_stripping_returns_no_messages() -> None:
    formatter = Formatter(max_chars=100)

    assert formatter.format("<reasoning>only</reasoning>") == []


def test_formatter_rejects_invalid_limit() -> None:
    try:
        Formatter(max_chars=0)
    except ValueError:
        pass
    else:
        raise AssertionError("max_chars=0 should fail")


def test_double_asterisk_becomes_whatsapp_bold() -> None:
    assert to_whatsapp("**Ringkas**: langsung ke inti") == "*Ringkas*: langsung ke inti"


def test_markdown_heading_becomes_bold_line() -> None:
    assert to_whatsapp("## Ringkasan") == "*Ringkasan*"
    assert to_whatsapp("### **Catatan**") == "*Catatan*"


def test_markdown_bullets_become_unicode_bullets() -> None:
    raw = "- satu\n* dua\n+ tiga"

    assert to_whatsapp(raw) == "• satu\n• dua\n• tiga"


def test_link_markdown_becomes_text_and_url() -> None:
    raw = "Sumber: [Detik.com](https://www.detik.com/berita)"

    assert to_whatsapp(raw) == "Sumber: Detik.com (https://www.detik.com/berita)"


def test_strikethrough_and_horizontal_rule() -> None:
    assert to_whatsapp("~~coret~~") == "~coret~"
    assert to_whatsapp("atas\n---\nbawah") == "atas\n\nbawah"


def test_code_spans_and_fences_are_unchanged() -> None:
    inline = "Pakai `**ini kode**` apa adanya."
    fenced = '```python\nprint("**x**")\n```'

    assert to_whatsapp(inline) == inline
    assert to_whatsapp(fenced) == fenced


def test_conversion_is_idempotent_for_whatsapp_syntax() -> None:
    text = "*tebal* dan _miring_ serta ~coret~"

    assert to_whatsapp(text) == text
    assert to_whatsapp(to_whatsapp("**tebal**")) == "*tebal*"


def test_formatter_converts_before_applying_limit(config) -> None:
    formatter = Formatter(max_chars=12)

    messages = formatter.format("**" + "x" * 30 + "**")

    assert len(messages) == 1
    assert len(messages[0]) <= 12
    assert "**" not in messages[0]


def test_formatter_sends_converted_markdown(config) -> None:
    formatter = Formatter(max_chars=100)

    assert formatter.format("## Halo\n\n**dunia**") == ["*Halo*\n\n*dunia*"]
