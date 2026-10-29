"""SafetyPolicy: gerbang deterministik sebelum tool sensitif (seam untuk robot fisik)."""

from __future__ import annotations

import fnmatch
import os
import re
import shlex
from dataclasses import dataclass
from typing import Protocol

from .config import ApprovalConfig
from .registry import allows

READ_ONLY = frozenset({"read", "memory", "skill", "chat_history", "web_search"})

SHELL_TOOL = "shell"
SHELL_WRAPPERS = frozenset(
    {
        "sudo",
        "env",
        "nohup",
        "nice",
        "ionice",
        "time",
        "command",
        "builtin",
        "exec",
        "stdbuf",
        "xargs",
    }
)
WRAPPER_VALUES = {
    "sudo": frozenset(
        {
            "-u",
            "-g",
            "-p",
            "-r",
            "-t",
            "-U",
            "-C",
            "-h",
            "--user",
            "--group",
            "--prompt",
            "--role",
            "--type",
            "--chdir",
            "--host",
        }
    ),
    "env": frozenset({"-u", "--unset", "-C", "--chdir", "-S", "--split-string"}),
    "nice": frozenset({"-n", "--adjustment"}),
    "ionice": frozenset({"-c", "-n", "-t", "-p", "--class", "--classdata", "--pid"}),
    "xargs": frozenset(
        {
            "-I",
            "-n",
            "-P",
            "-L",
            "-s",
            "-d",
            "-E",
            "-a",
            "--replace",
            "--max-args",
            "--max-procs",
            "--max-lines",
            "--max-chars",
            "--delimiter",
            "--eof",
            "--arg-file",
        }
    ),
    "exec": frozenset({"-a"}),
    "stdbuf": frozenset({"-i", "-o", "-e"}),
}
SHELL_INTERPRETERS = frozenset({"bash", "sh", "zsh", "dash", "ksh"})
SHELL_OPERATORS = frozenset({";", "&&", "||", "|", "&", "(", ")"})
DESTRUCTIVE_ACTIONS = {
    "memory": frozenset({"forget"}),
    "skill": frozenset({"archive"}),
}
MAX_DEPTH = 3
_ASSIGNMENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*=.*")
_SUBSTITUTION = re.compile(r"\$\(([^()]*)\)|`([^`]*)`")


@dataclass(frozen=True)
class ActionRequest:
    tool: str
    args: dict
    chat_id: str
    sender: str
    gateway: str
    roles: tuple[str, ...]


@dataclass(frozen=True)
class SafetyDecision:
    verdict: str  # "allow" | "pending" | "deny"
    reason: str = ""


class SafetyPolicy(Protocol):
    def check(self, request: ActionRequest) -> SafetyDecision: ...


class AllowAllSafety:
    """approval.mode=off: semua tool langsung (dokumentasikan risikonya)."""

    def check(self, request: ActionRequest) -> SafetyDecision:
        return SafetyDecision("allow")


def _tokens(text: str) -> list[str]:
    lex = shlex.shlex(text, posix=True, punctuation_chars=True)
    lex.whitespace_split = True
    lex.commenters = ""
    return list(lex)


def _segments(tokens: list[str]) -> list[list[str]]:
    found: list[list[str]] = []
    current: list[str] = []
    for token in tokens:
        if token in SHELL_OPERATORS:
            if current:
                found.append(current)
                current = []
        else:
            current.append(token)
    if current:
        found.append(current)
    return found


def _sensitive_segment(tokens: list[str], patterns: tuple[str, ...], depth: int) -> bool:
    tokens = list(tokens)
    while True:
        while tokens and _ASSIGNMENT.fullmatch(tokens[0]):
            tokens.pop(0)
        if not tokens:
            return False
        head = os.path.basename(tokens[0])
        if head not in SHELL_WRAPPERS:
            break
        values = WRAPPER_VALUES.get(head, frozenset())
        tokens.pop(0)
        while tokens and tokens[0].startswith("-"):
            option = tokens.pop(0)
            if option in values and tokens:
                tokens.pop(0)
    head = os.path.basename(tokens[0])
    if head in SHELL_INTERPRETERS:
        for index, token in enumerate(tokens[1:], 1):
            if token.startswith("-") and not token.startswith("--") and "c" in token:
                if index + 1 >= len(tokens):
                    return True
                return _scan(tokens[index + 1], patterns, depth + 1)
    text = " ".join([head, *tokens[1:]]) + " "
    return any(fnmatch.fnmatchcase(text, pattern) for pattern in patterns)


def _scan(text: str, patterns: tuple[str, ...], depth: int = 0) -> bool:
    if depth > MAX_DEPTH:
        return True
    for line in text.splitlines() or [text]:
        remainder = line
        for match in _SUBSTITUTION.finditer(line):
            inner = match.group(1) if match.group(1) is not None else (match.group(2) or "")
            if _scan(inner, patterns, depth + 1):
                return True
            remainder = remainder.replace(match.group(0), " ", 1)
        if "$(" in remainder or "`" in remainder:
            return True
        try:
            tokens = _tokens(remainder)
        except ValueError:
            return True
        for segment in _segments(tokens):
            if _sensitive_segment(segment, patterns, depth):
                return True
    return False


class ApprovalPolicy:
    def __init__(self, config: ApprovalConfig) -> None:
        self.configure(config)

    def configure(self, config: ApprovalConfig) -> None:
        self.config = config

    def check(self, request: ActionRequest) -> SafetyDecision:
        if self.config.mode == "off":
            return SafetyDecision("allow")
        if self.config.mode == "strict" and request.tool not in READ_ONLY:
            return SafetyDecision("pending", "strict mode: semua aksi menunggu /approve")
        if allows(tuple(self.config.sensitive_tools), request.tool):
            return SafetyDecision(
                "pending", f"{request.tool} adalah tool sensitif; butuh /approve"
            )
        if request.tool == SHELL_TOOL and _scan(
            str(request.args.get("command") or ""), self.config.sensitive_commands
        ):
            return SafetyDecision("pending", "shell command is sensitive; butuh /approve")
        actions = DESTRUCTIVE_ACTIONS.get(request.tool)
        if actions and str(request.args.get("action") or "").strip().lower() in actions:
            return SafetyDecision(
                "pending",
                f"{request.tool} {request.args.get('action')} is destructive; butuh /approve",
            )
        return SafetyDecision("allow")


def build_safety(config: ApprovalConfig) -> SafetyPolicy:
    if config.mode == "off":
        return AllowAllSafety()
    return ApprovalPolicy(config)
