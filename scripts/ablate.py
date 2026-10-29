#!/usr/bin/env python3
"""Ablasi perilaku Sanka dari arsip di database: replay gerbang, anggaran prompt, keputusan.

Harness read-only: tidak mengirim pesan, tidak memanggil `handle`, dan tidak menulis state.
Jalankan dari root repo: `.venv/bin/python scripts/ablate.py --last 100`.
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

from sanka.engine import ablate
from sanka.engine.config import load_config, load_env, load_host, resolve_database_url
from sanka.engine.db import Database
from sanka.engine.llm import LLM
from sanka.engine.memory import build_memory
from sanka.engine.stores.affect import AffectStore
from sanka.engine.stores.documents import DocumentStore
from sanka.engine.stores.history import HistoryStore
from sanka.engine.stores.skills import SkillStore
from sanka.engine.stores.style import StyleStore


def open_stores(config, db, memory) -> dict:
    stores: dict = {"memory": memory}
    with suppress(Exception):
        stores["skills"] = SkillStore(db, config.skills.max_index_chars)
    if config.affect.enabled:
        with suppress(Exception):
            stores["affect"] = AffectStore(db, config.affect, soul=config.soul.persona)
    if config.style.enabled:
        with suppress(Exception):
            stores["style"] = StyleStore(db, config.style)
    return stores


def blocks_for(chat: str, stores: dict):
    memory = stores.get("memory")
    skills = stores.get("skills")
    affect = stores.get("affect")
    style = stores.get("style")

    def build(message) -> dict:
        blocks = dict.fromkeys(ablate.salience.COMPETING, "")
        if memory is not None and getattr(memory, "available", False):
            with suppress(Exception):
                blocks["memory"] = asyncio.run(
                    memory.context(
                        person_id=message.sender_id or "",
                        chat_id=chat,
                        query=message.text,
                    )
                )
            with suppress(Exception):
                blocks["actions"] = ""  # ledger dipisah; ablasi ini fokus blok prompt
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
    parser = argparse.ArgumentParser(description="Ablasi perilaku Sanka dari arsip database.")
    parser.add_argument("--config", default=None, help="path config.yaml")
    parser.add_argument("--chat", default=None, help="JID chat (default: chat terbaru)")
    parser.add_argument("--last", type=int, default=200, help="jumlah pesan arsip (default 200)")
    parser.add_argument(
        "--llm-sample", type=int, default=0, help="jumlah pesan untuk replay keputusan LLM"
    )
    parser.add_argument(
        "--ablate", default="emotion,vibe,away", help="blok gate yang diabsi (emotion,vibe,away)"
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
    host, paths = load_host(args.config)
    url = resolve_database_url(host.database)
    if not url:
        raise SystemExit("database URL kosong")
    config = load_config(args.config, require_key=False)
    db = Database(url, pool_size=host.database.pool_size, pool_timeout=host.database.pool_timeout)
    documents = DocumentStore(db, paths, sync=host.sync_documents)
    documents.seed()
    history = HistoryStore(db, config.workspace)
    chat = args.chat or next(iter(history.recent_chats(1)), "")
    if not chat:
        db.dispose()
        raise SystemExit("arsip kosong: belum ada chat untuk diabsi")
    messages = history.recent(chat, args.last)
    if not messages:
        db.dispose()
        raise SystemExit(f"chat {chat} tidak punya pesan di arsip")
    rows = ablate.classify(messages, bot_name=config.soul.name)
    memory = asyncio.run(build_memory(config, db=db))
    stores = open_stores(config, db, memory)
    try:
        budget = ablate.prompt_budget(
            config,
            messages,
            sample=0,
            blocks_for=blocks_for(chat, stores),
        )
        shift = None
        if args.llm_sample > 0:
            if not os.environ.get(config.llm.api_key_env):
                print(
                    f"lewati decision_shift: env {config.llm.api_key_env} belum diisi",
                    flush=True,
                )
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
    finally:
        asyncio.run(memory.close())
        db.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
