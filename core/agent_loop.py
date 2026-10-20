# Copyright (c) 2025 lsdefine
#
# Adapted from GenericAgent's agent_loop.py
# (upstream commit e6b1bcec92e7789eb80975980b5fd47a2abea860), MIT License.
# The upstream copyright notice is retained; modifications are marked below
# with ``SANKA-MOD:`` comments.

"""Agent turn loop adapted from GenericAgent.

SANKA-MOD: trimmed adaptation for Sanka's plan-2 runtime. The loop drives an
LLM client that streams text chunks and finally returns a response object with
``.content`` and ``.tool_calls``. No tool implementations live here: a handler
decides what each turn means and when the loop should stop.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass
from typing import Any, Callable, Generator, Iterator


def _hook(event: str, context: dict[str, Any] | None = None) -> None:
    """SANKA-MOD: no-op stand-in for ``plugins.hooks.trigger``.

    Sanka does not ship a plugins package yet, so every upstream hook call
    site resolves to this function instead of importing an optional module.
    """
    return None


@dataclass
class StepOutcome:
    """Result of one handler step."""

    data: Any
    next_prompt: str | None = None
    should_exit: bool = False


def try_call_generator(
    func: Callable[..., Any], *args: Any, **kwargs: Any
) -> Generator[Any, None, Any]:
    """Call ``func`` and delegate to it when it returns a generator."""
    ret = func(*args, **kwargs)
    if hasattr(ret, "__iter__") and not isinstance(ret, (str, bytes, dict, list)):
        ret = yield from ret
    return ret


class BaseHandler:
    """Default handler contract used by :func:`agent_runner_loop`."""

    def turn_end_callback(
        self,
        response: Any,
        tool_calls: list[dict[str, Any]],
        tool_results: list[dict[str, Any]],
        turn: int,
        next_prompt: str,
        exit_reason: dict[str, Any],
    ) -> str:
        return next_prompt

    def dispatch(
        self,
        tool_name: str,
        args: dict[str, Any],
        response: Any,
        index: int = 0,
        tool_num: int = 1,
    ) -> Generator[str, None, StepOutcome]:
        """Route one turn to ``do_<tool_name>`` when it exists."""
        method_name = f"do_{tool_name}"
        if hasattr(self, method_name):
            args["_index"] = index
            args["_tool_num"] = tool_num
            _hook("tool_before", locals())
            ret = yield from try_call_generator(getattr(self, method_name), args, response)
            _hook("tool_after", locals())
            return ret
        if tool_name == "bad_json":
            return StepOutcome(
                None, next_prompt=args.get("msg", "bad_json"), should_exit=False
            )
        yield f"未知工具: {tool_name}\n"
        return StepOutcome(None, next_prompt=f"未知工具 {tool_name}", should_exit=False)


def json_default(o: Any) -> Any:
    return list(o) if isinstance(o, set) else str(o)


def exhaust(g: Iterator[Any]) -> Any:
    """Consume a generator and return its ``StopIteration`` value."""
    try:
        while True:
            next(g)
    except StopIteration as e:
        return e.value


def get_pretty_json(data: Any) -> str:
    if isinstance(data, dict) and "script" in data:
        data = data.copy()
        data["script"] = data["script"].replace("; ", ";\n  ")
    return json.dumps(data, indent=2, ensure_ascii=False).replace("\\n", "\n")


def agent_runner_loop(
    client: Any,
    system_prompt: str,
    user_input: str,
    handler: BaseHandler,
    tools_schema: Any,
    max_turns: int = 40,
    verbose: bool = False,
    initial_user_content: str | list[dict[str, Any]] | None = None,
    yield_info: bool = False,
    *,
    history: list[dict[str, str]] | None = None,
) -> Generator[Any, None, dict[str, Any]]:
    """Run the LLM turn loop until the handler exits or turns run out.

    SANKA-MOD: ``verbose`` defaults to ``False`` for Sanka and the new
    keyword-only ``history`` parameter is spliced between the system prompt and
    the current user message: ``[system, *history, user]``. Plan-12 also accepts
    OpenAI-style content parts for the current user turn (images), while the
    stored history remains plain text.
    """
    initial_content = initial_user_content if initial_user_content is not None else user_input
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system_prompt},
        *(history or []),
        {"role": "user", "content": initial_content},
    ]
    turn = 0
    handler.max_turns = max_turns
    exit_reason: dict[str, Any] = {}
    # SANKA-MOD: initialize the loop locals so a zero-turn run cannot leak
    # unbound names into turn_end_callback below.
    response: Any = None
    tool_calls: list[dict[str, Any]] = []
    tool_results: list[dict[str, Any]] = []
    # SANKA-MOD: read the session directory defensively; handlers without a
    # parent (or a parent without task_dir) must not crash the loop.
    task_dir = getattr(getattr(handler, "parent", None), "task_dir", None)
    _hook("agent_before", locals())
    while turn < handler.max_turns:
        turn += 1
        turnstr = f"LLM Running (Turn {turn}) ..."
        if task_dir:
            turnstr = f"Turn {turn} ..."
        if verbose:
            turnstr = f"**{turnstr}**"
        if yield_info:
            yield {"turn": turn}
        yield f"\n{turnstr}\n\n"
        if turn % 10 == 0 and hasattr(client, "last_tools"):
            # SANKA-MOD: only reset the cache when the client exposes it.
            client.last_tools = ""
        _hook("turn_before", locals())
        _hook("llm_before", locals())
        response_gen = client.chat(messages=messages, tools=tools_schema)
        if verbose:
            response = yield from response_gen
            yield "\n\n"
        else:
            response = exhaust(response_gen)
            cleaned = _clean_content(response.content)
            if cleaned:
                yield cleaned + "\n"
        _hook("llm_after", locals())

        if not response.tool_calls:
            tool_calls = [{"tool_name": "no_tool", "args": {}}]
        else:
            tool_calls = [
                {
                    "tool_name": tc.function.name,
                    "args": json.loads(tc.function.arguments),
                    "id": tc.id,
                }
                for tc in response.tool_calls
            ]

        tool_results = []
        next_prompts: set[str] = set()
        exit_reason = {}
        for ii, tc in enumerate(tool_calls):
            tool_name, args, tid = tc["tool_name"], tc["args"], tc.get("id", "")
            if tool_name == "no_tool":
                pass
            elif verbose:
                yield (
                    f"🛠️ Tool: `{tool_name}`  📥 args:\n````text\n"
                    f"{get_pretty_json(args)}\n````\n"
                )
            else:
                yield f"🛠️ {tool_name}({_compact_tool_args(tool_name, args)})\n"
            handler.current_turn = turn
            gen = handler.dispatch(
                tool_name, args, response, index=ii, tool_num=len(tool_calls)
            )
            try:
                v = next(gen)
                if verbose:
                    yield "`````\n" + v
                outcome = (yield from gen) if verbose else exhaust(gen)
                if verbose:
                    yield "`````\n"
            except StopIteration as e:
                outcome = e.value

            if outcome.should_exit:
                exit_reason = {"result": "EXITED", "data": outcome.data}
                break
            if not outcome.next_prompt:
                exit_reason = {"result": "CURRENT_TASK_DONE", "data": outcome.data}
                break
            if outcome.next_prompt.startswith("未知工具"):
                if hasattr(client, "last_tools"):
                    client.last_tools = ""
            if outcome.data is not None and tool_name != "no_tool":
                datastr = (
                    json.dumps(outcome.data, ensure_ascii=False, default=json_default)
                    if type(outcome.data) in (dict, list)
                    else str(outcome.data)
                )
                tool_results.append({"tool_use_id": tid, "content": datastr})
            next_prompts.add(outcome.next_prompt)
        if len(next_prompts) == 0 or exit_reason:
            # SANKA-MOD: tolerate handlers that never define ``_done_hooks``.
            done_hooks = getattr(handler, "_done_hooks", [])
            if len(done_hooks) == 0 or exit_reason.get("result", "") == "EXITED":
                break
            next_prompts.add(done_hooks.pop(0))
        next_prompt = handler.turn_end_callback(
            response, tool_calls, tool_results, turn, "\n".join(next_prompts), exit_reason
        )
        _hook("turn_after", locals())
        # SANKA-MOD: only the new user message is appended; the session keeps history.
        messages = [
            {"role": "user", "content": next_prompt, "tool_results": tool_results}
        ]
    if exit_reason:
        handler.turn_end_callback(
            response, tool_calls, tool_results, turn, "", exit_reason
        )
    _hook("agent_after", locals())
    return exit_reason or {"result": "MAX_TURNS_EXCEEDED"}


def _clean_content(text: str | None) -> str:
    if not text:
        return ""

    def _shrink_code(m: re.Match[str]) -> str:
        lines = m.group(0).split("\n")
        lang = lines[0].replace("```", "").strip()
        body = [line for line in lines[1:-1] if line.strip()]
        if len(body) <= 6:
            return m.group(0)
        preview = "\n".join(body[:5])
        return f"```{lang}\n{preview}\n  ... ({len(body)} lines)\n```"

    text = re.sub(r"```[\s\S]*?```", _shrink_code, text)
    for pattern in [
        r"<file_content>[\s\S]*?</file_content>",
        r"<tool_(?:use|call)>[\s\S]*?</tool_(?:use|call)>",
        r"(\r?\n){3,}",
    ]:
        text = re.sub(pattern, "\n\n" if "\\n" in pattern else "", text)
    return text.strip()


def _compact_tool_args(name: str, args: dict[str, Any]) -> str:
    compact = {k: v for k, v in args.items() if k != "_index"}
    for key in ("path",):
        if key in compact:
            compact[key] = os.path.basename(compact[key])
    if name == "update_working_checkpoint":
        text = compact.get("key_info", "")
        return (text[:60] + "...") if len(text) > 60 else text
    if name == "ask_user":
        question = str(compact.get("question", ""))
        candidates = compact.get("candidates") or []
        if candidates:
            question += "\ncandidates:\n" + "\n".join(f"- {c}" for c in candidates)
        return question
    dumped = json.dumps(compact, ensure_ascii=False)
    return (dumped[:120] + "...") if len(dumped) > 120 else dumped
