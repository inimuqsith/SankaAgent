#!/usr/bin/env python3
"""Ablasi perilaku Sanka dari arsip lokal: replay gerbang, anggaran prompt, keputusan.

Jalankan dari root repo: `.venv/bin/python scripts/ablate.py --last 100`.
Harness read-only: tidak mengirim pesan, tidak memanggil `handle_message`, dan tidak
menulis state runtime. `--llm-sample N` memanggil LLM sungguhan lewat `decide` saja.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from contextlib import suppress
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sanka import ablate
from sanka.affect import AffectStore
from sanka.config import load_config, load_env
from sanka.history import HistoryStore
from sanka.llm import LLM
from sanka.memory import MemoryStore
from sanka.skills import SkillStore
from sanka.style import StyleStore


def blocks_for(chat: str, stores: dict):
    """Blok prompt opsional per pesan; sumber yang gagal menjadi string kosong."""
    memory = stores.get("memory")
    skills = stores.get("skills")
    affect = stores.get("affect")
    style = stores.get("style")

    def build(message) -> dict:
        blocks = dict.fromkeys(ablate.salience.COMPETING, "")
        if memory is not None:
            with suppress(Exception):
                blocks["memory"] = memory.context(chat, include_global=True, query=message.text)
            with suppress(Exception):
                blocks["actions"] = memory.actions_context()
        if skills is not None:
            with suppress(Exception):
                blocks["skills"] = skills.index()
        if affect is not None:
            with suppress(Exception):
                blocks["topic"] = affect.topic_block(chat)
        if style is not None:
            with suppress(Exception):
                blocks["style"] = style.profile(chat)
            with suppress(Exception):
                blocks["person_style"] = style.person_profile(message.sender_id)
        return blocks

    return build


def open_stores(config) -> dict:
    """Buka store yang datanya memang sudah ada; konstruktor tidak boleh membuat state baru."""
    stores: dict = {}
    if config.memory.path.is_file():
        stores["memory"] = MemoryStore(
            config.memory.path, config.memory.max_items, config.memory.max_chars
        )
    if config.skills.dir.is_dir():
        stores["skills"] = SkillStore(config.skills.dir, config.skills.max_index_chars)
    if config.affect.enabled and config.affect.path.is_file():
        stores["affect"] = AffectStore(config.affect.path, config.affect, soul=config.persona)
    if config.style.enabled and config.style.path.is_file():
        stores["style"] = StyleStore(config.style.path, config.style)
    return stores


def report(chat: str, replay: dict, budget: dict, shift: dict | None) -> str:
    lines = [
        f"chat    : {chat}",
        (
            "replay  : "
            f"incoming={replay['incoming']} called={replay['called']} "
            f"replied={replay['replied']} "
            f"false_positive={replay['false_positive']} missed={replay['missed']} "
            f"unprompted_rate={replay['unprompted_rate']:.3f}"
        ),
        (
            "prompt  : "
            f"samples={budget['samples']} before_mean={budget['before_mean']:.0f} "
            f"after_mean={budget['after_mean']:.0f} saved_mean={budget['saved_mean']:.0f}"
        ),
        "dibuang : "
        + (", ".join(f"{name}={count}" for name, count in budget["dropped"].items()) or "-"),
    ]
    if shift is not None:
        lines.append(
            "decision: "
            f"samples={shift['samples']} addressed_changed={shift['addressed_changed']} "
            f"act_changed={shift['act_changed']} tone_changed={shift['tone_changed']}"
        )
        for example in shift["examples"]:
            lines.append(f"  {example['text'][:80]!r}: {example['before']} -> {example['after']}")
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Ablasi perilaku Sanka dari arsip lokal.")
    parser.add_argument("--chat", default=None, help="JID chat (default: chat terbaru)")
    parser.add_argument("--last", type=int, default=200, help="jumlah pesan arsip (default 200)")
    parser.add_argument(
        "--llm-sample", type=int, default=0, help="jumlah pesan untuk replay keputusan LLM"
    )
    parser.add_argument(
        "--ablate",
        default="emotion,vibe,away",
        help="blok gate yang diabsi (emotion,vibe,away)",
    )
    parser.add_argument("--json", type=Path, default=None, help="tulis hasil lengkap ke path JSON")
    args = parser.parse_args()

    names = tuple(name.strip() for name in args.ablate.split(",") if name.strip())
    unknown = [name for name in names if name not in ablate.ABLATABLE]
    if unknown:
        raise SystemExit(f"blok tidak bisa diabsi: {', '.join(unknown)}")
    if args.llm_sample < 0 or args.last <= 0:
        raise SystemExit("--last harus > 0 dan --llm-sample tidak boleh negatif")

    load_env()
    config = load_config(require_key=False)  # replay/prompt tetap jalan tanpa API key
    history_path = config.whatsapp.history_db
    if not history_path.is_file():
        raise SystemExit(f"arsip tidak ditemukan: {history_path}")
    store = HistoryStore(history_path, config.workspace, config.whatsapp.session_db)
    chat = args.chat or next(iter(store.recent_chats(1)), "")
    if not chat:
        raise SystemExit("arsip kosong: belum ada chat untuk diabsi")
    messages = store.recent(chat, args.last)
    if not messages:
        raise SystemExit(f"chat {chat} tidak punya pesan di arsip")
    rows = ablate.classify(messages, bot_name=config.whatsapp.bot_name)
    stores = open_stores(config)
    budget = ablate.prompt_budget(
        config,
        messages,
        sample=0,
        blocks_for=blocks_for(chat, stores),
    )
    shift = None
    if args.llm_sample > 0:
        if not os.environ.get(config.llm.api_key_env):
            print(f"lewati decision_shift: env {config.llm.api_key_env} belum diisi", flush=True)
        else:
            shift = asyncio.run(
                ablate.decision_shift(
                    LLM(config),
                    config,
                    rows,
                    sample=args.llm_sample,
                    ablate=names,
                    chat_id=chat,
                    history=messages,
                    affect=stores.get("affect"),
                )
            )
    print(report(chat, ablate.replay_metrics(rows), budget, shift))
    if args.json is not None:
        payload = {
            "chat": chat,
            "replay": ablate.replay_metrics(rows),
            "prompt_budget": budget,
            "decision_shift": shift,
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"json    : {args.json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
