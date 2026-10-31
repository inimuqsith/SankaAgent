#!/usr/bin/env python3
"""Replay skenario scripted terhadap prompt turn produksi.

Sumber skenario: `tests/data/scenarios.json` (turns, target per pembicara, rubrik, contoh
buruk). Prompt dirakit seperti produksi: `system_prompt` + `character_block` dengan pola
dari `sanka/data/patterns.json`, lalu `regulate` menahan balasan yang melewati level.
Skrip ini tidak pernah menulis ke skenario atau katalog; keluarannya transkrip + ringkasan
pass/fail di `workspace/replay-<id>.json`.

    python scripts/replay.py --scenario roast_group --limit 4
    python scripts/replay.py --no-regulate --no-judge --limit 6
    python scripts/replay.py --export workspace/replay-all.json
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from _paths import safe_path  # noqa: E402

from sanka.engine.character import (  # noqa: E402
    ASSISTANT_TELL,
    MARKDOWN,
    effective_limits,
    regulate,
)
from sanka.engine.config import load_config, load_env  # noqa: E402
from sanka.engine.context import (  # noqa: E402
    AUTHOR_BOT,
    AUTHOR_UNKNOWN,
    ChatMessage,
    build_user_content,
)
from sanka.engine.llm import LLM  # noqa: E402
from sanka.engine.patterns import room_register, select_patterns  # noqa: E402
from sanka.engine.prompts import character_block, system_prompt  # noqa: E402
from sanka.engine.reply import clean_prose, human_chunks, strip_blocks  # noqa: E402
from sanka.engine.roles import Role  # noqa: E402
from sanka.engine.runtime import RuntimeFacts  # noqa: E402
from sanka.engine.target import combine, score_style  # noqa: E402
from sanka.engine.tools import ToolContext  # noqa: E402

SCENARIOS = ROOT / "tests" / "data" / "scenarios.json"
OUT = ROOT / "workspace"
MAX_WINDOW = 20
MAX_BUBBLES = 3
BUBBLE_MAX = 240
TURN_MAX = 400
SHINGLE = 5
JUDGE_SYSTEM = (
    "You score one WhatsApp reply from Sanka, an Indonesian persona, against a rubric. Answer with "
    'one JSON object only: {"scores": {<dimension>: <1-5>}}.\n'
    "Score every dimension in the payload with an integer 1-5; 5 only when the reply is clearly "
    "good at that dimension. Hard fails are measured elsewhere, so do not report them. No prose."
)
CATEGORY_PASS = {
    "serious_dm": "serious_average_min",
    "romance_creative": "romance_creative_average_min",
    "roast_group": "roast_average_min",
}
JSON_OBJECT = re.compile(r"\{.*\}", re.DOTALL)


def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", str(text or "").lower())


def shingles(text: str) -> set[tuple[str, ...]]:
    found = words(text)
    return {tuple(found[index : index + SHINGLE]) for index in range(len(found) - SHINGLE + 1)}


def message(text: str, *, speaker: str, when: datetime, index: int, author: str = AUTHOR_UNKNOWN):
    return ChatMessage(
        id=f"replay-{index}",
        sender_name=speaker,
        sender_id="owner" if speaker == "Muqsith" else speaker.lower(),
        sender_jid="",
        when=when,
        text=text,
        outgoing=author == AUTHOR_BOT,
        author=author,
    )


def bubbles_of(answer: str) -> list[str]:
    if not answer:
        return []
    found = [clean_prose(part) for part in human_chunks(answer, max_parts=MAX_BUBBLES)]
    return [part for part in found if part]


def hard_fails(answer: str, bubbles: list[str], seen: set) -> list[str]:
    """Metrik deterministik dari rubrik: tidak butuh LLM, tidak bisa dibujuk."""
    if not str(answer or "").strip():
        return ["empty_reply"]
    fails: list[str] = []
    if ASSISTANT_TELL.search(answer):
        fails.append("assistant_tell")
    if MARKDOWN.search(answer):
        fails.append("markdown_list_or_heading")
    if len(bubbles) > MAX_BUBBLES:
        fails.append("too_many_bubbles")
    if any(len(part) > BUBBLE_MAX for part in bubbles) or len(answer) > TURN_MAX:
        fails.append("too_long")
    if shingles(answer) & seen:
        fails.append("repeated_phrase")
    return fails


def resolve_target(scenario: dict, speaker: str, recent: list[str]) -> tuple[str, float]:
    declared = str((scenario.get("targets") or {}).get(speaker, "")).lower()
    if declared in ("male", "female"):
        return declared, 1.0
    target, confidence, _ = combine(score_style(recent), None, gate=True)
    return target, confidence


def judge_payload(sheet: dict, scenario: dict, transcript: list[str], reply: str) -> str:
    dimensions = "\n".join(
        f"- {item['id']}: {item['question']}" for item in sheet["rubric"]["dimensions"]
    )
    fails = "\n".join(f"- {item}" for item in sheet["rubric"]["hard_fails"])
    return (
        f"Scenario: {scenario['title']} ({scenario['chat']}). Goal: {scenario['goal']}\n"
        f"Transcript so far:\n" + "\n".join(transcript[-12:]) + "\n\n"
        f"Reply to judge:\n{reply}\n\nDimensions:\n{dimensions}\n\nHard fails:\n{fails}"
    )


def parse_judge(raw: str) -> dict | None:
    found = JSON_OBJECT.search(str(raw or ""))
    if not found:
        return None
    try:
        body = json.loads(found.group(0))
    except ValueError:
        return None
    scores = body.get("scores") if isinstance(body.get("scores"), dict) else {}
    return {str(key): value for key, value in scores.items() if isinstance(value, (int, float))}


def judge_verdict(sheet: dict, scenario: dict, scores: dict) -> dict:
    """Rubrik menentukan lulus/gagal; model hanya memberi skor per dimensi."""
    rubric = sheet.get("rubric") or {}
    limits = rubric.get("pass") or {}
    values = [float(value) for value in scores.values()]
    if not values:
        return {"scores": scores, "verdict": "unavailable", "average": None, "floor": None}
    key = CATEGORY_PASS.get(scenario["id"], "")
    floor = float(limits.get(key) or limits.get("average_min") or 0)
    average = sum(values) / len(values)
    passed = average >= floor and min(values) >= float(limits.get("dimension_min") or 0)
    return {
        "scores": scores,
        "average": round(average, 2),
        "floor": floor,
        "verdict": "pass" if passed else "fail",
    }


async def judge(llm: LLM, sheet: dict, scenario: dict, transcript: list[str], reply: str) -> dict:
    try:
        raw = await llm.complete(
            JUDGE_SYSTEM,
            judge_payload(sheet, scenario, transcript, reply),
            max_tokens=800,
            temperature=0.0,
            reasoning_effort="minimal",
            session=f"replay-judge-{scenario['id']}",
        )
    except Exception:
        return judge_verdict(sheet, scenario, {})
    return judge_verdict(sheet, scenario, parse_judge(raw) or {})


async def reply_once(
    config, llm: LLM, scenario: dict, window: list, current, transcript: list
) -> dict:
    recent = [entry.text for entry in window[-8:] if entry.text.strip()]
    target, confidence = resolve_target(scenario, current.sender_name, recent)
    limits = effective_limits(config.character, target, confidence)
    register = room_register([entry.text for entry in window[-6:] if entry.text.strip()])
    patterns = select_patterns(
        config.character.patterns,
        register=register,
        target=target,
        limits=limits,
        messages=recent,
    )
    block = character_block(
        limits,
        target,
        confidence,
        gate=config.character.gender_gate,
        jabs=config.character.roast_jabs,
        patterns=patterns,
    )
    system = system_prompt(
        config,
        (Role("owner"),),
        runtime=RuntimeFacts(mode="primary", gateways=("replay",), db_label="replay"),
        gateway_hints=(
            "## Chat interface\n- WhatsApp formatting only: *bold*, _italic_. No headings."
        ),
        sender=current.sender_name,
        is_dm=scenario["chat"] == "dm",
        mode="daily",
        tone="neutral",
        decision_reason="replay turn; reply",
        query=current.text,
        character=block,
    )
    content = await build_user_content(
        config,
        f"replay-{'dm' if scenario['chat'] == 'dm' else 'group'}",
        window[-MAX_WINDOW:],
        current,
        chat_name="replay",
    )
    ctx = ToolContext(config, (Role("owner"),), (), chat_id="replay", sender=current.sender_name)
    answer = strip_blocks(
        str(await llm.run(system, content, [], ctx, session=f"replay-{scenario['id']}"))
    ).strip()
    return {
        "target": target,
        "confidence": confidence,
        "limits": limits,
        "register": register,
        "patterns": patterns,
        "answer": answer,
    }


async def run_scenario(
    config, sheet: dict, scenario: dict, *, limit: int | None, use_judge: bool, use_regulate: bool
) -> dict:
    llm = LLM(config)
    window: list = []
    transcript: list[str] = []
    turns_out: list[dict] = []
    seen: set = set()
    now = datetime(2026, 11, 1, 20, 0, 0)
    index = 0
    turns = scenario["turns"][:limit] if limit else scenario["turns"]
    for number, turn in enumerate(turns, start=1):
        index += 1
        current = message(
            turn["text"],
            speaker=turn["speaker"],
            when=now + timedelta(minutes=number * 2),
            index=index,
        )
        window.append(current)
        transcript.append(f"{turn['speaker']}: {turn['text']}")
        result = await reply_once(config, llm, scenario, window, current, transcript)
        answer = result["answer"]
        if answer and use_regulate:
            answer = await regulate(
                llm,
                config,
                answer,
                target=result["target"],
                confidence=result["confidence"],
                latest=current.text,
                session=f"replay-regulate-{scenario['id']}",
            )
        parts = bubbles_of(answer)
        visible = "\n".join(parts) or answer
        fails = hard_fails(answer, parts, seen) if answer else ["empty_reply"]
        seen |= shingles(answer)
        for part in parts:
            index += 1
            window.append(
                message(
                    part,
                    speaker="Sanka",
                    when=now + timedelta(minutes=number * 2, seconds=15),
                    index=index,
                    author=AUTHOR_BOT,
                )
            )
        transcript.append("Sanka: " + (visible or "(kosong)"))
        judged = (
            await judge(llm, sheet, scenario, transcript, visible)
            if visible and use_judge
            else None
        )
        turns_out.append(
            {
                "turn": number,
                "speaker": turn["speaker"],
                "text": turn["text"],
                "expect": turn.get("expect") or [],
                "target": result["target"],
                "limits": list(result["limits"]),
                "register": result["register"],
                "patterns": result["patterns"],
                "reply": visible,
                "hard_fail": fails,
                "judge": judged,
            }
        )
        flag = "FAIL " + ",".join(fails) if fails else "ok"
        print(f"  {number:>2} {turn['speaker']:<8} {flag:<28} {visible[:70]}")
    hard = sum(len(turn["hard_fail"]) for turn in turns_out)
    return {
        "scenario": scenario["id"],
        "title": scenario["title"],
        "chat": scenario["chat"],
        "turns": turns_out,
        "summary": {
            "turns": len(turns_out),
            "hard_fail": hard,
            "curse_lines": sum(
                1
                for turn in turns_out
                if re.search(r"\b(jancok|asu|goblok|cok)\b", turn["reply"].lower())
            ),
        },
    }


async def main_async(args) -> int:
    load_env()
    config = load_config("config.yaml")
    sheet = json.loads(SCENARIOS.read_text(encoding="utf-8"))
    chosen = [
        item for item in sheet["scenarios"] if not args.scenario or item["id"] == args.scenario
    ]
    if not chosen:
        print(f"skenario tidak ada: {args.scenario}", file=sys.stderr)
        return 2
    reports = []
    for scenario in chosen:
        print(f"scenario {scenario['id']} ({len(scenario['turns'])} turns)")
        report = await run_scenario(
            config,
            sheet,
            scenario,
            limit=args.limit,
            use_judge=not args.no_judge,
            use_regulate=not args.no_regulate,
        )
        target = (
            safe_path(OUT.parent, Path(args.export))
            if args.export
            else OUT / f"replay-{scenario['id']}.json"
        )
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(report, ensure_ascii=False, indent=2) + "\n"
        target.write_text(payload, encoding="utf-8")
        summary = report["summary"]
        print(f"  -> {target} hard_fail={summary['hard_fail']} curses={summary['curse_lines']}")
        reports.append(report)
    total = sum(report["summary"]["hard_fail"] for report in reports)
    print(f"summary scenarios={len(reports)} hard_fail={total}")
    return 1 if total else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Replay skenario Sanka")
    parser.add_argument("--scenario", help="hanya satu id skenario")
    parser.add_argument("--limit", type=int, help="batasi jumlah turn per skenario")
    parser.add_argument(
        "--export", help="tulis laporan ke path ini, bukan workspace/replay-<id>.json"
    )
    parser.add_argument("--no-judge", action="store_true", help="lewati penilaian LLM per turn")
    parser.add_argument("--no-regulate", action="store_true", help="lewati penahan level")
    return asyncio.run(main_async(parser.parse_args(argv)))


if __name__ == "__main__":
    raise SystemExit(main())
