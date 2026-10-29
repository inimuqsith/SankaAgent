"""Kontrak klasifikasi approval: hanya shell destruktif dan aksi internal yang menunggu."""

from __future__ import annotations

import pytest

from sanka.engine.config import ApprovalConfig
from sanka.engine.safety import ActionRequest, ApprovalPolicy, build_safety


def request(tool: str, **args) -> ActionRequest:
    return ActionRequest(
        tool=tool,
        args=args,
        chat_id="c",
        sender="s",
        gateway="g",
        roles=("owner",),
    )


def verdict(config: ApprovalConfig, tool: str, **args) -> str:
    return ApprovalPolicy(config).check(request(tool, **args)).verdict


ALLOWED_SHELL = [
    "ls -la",
    "git status",
    "printf sanka-ok",
    "python -c 'print(1)'",
    "echo hi > notes.txt",
]

BLOCKED_SHELL = [
    "rm -rf x",
    "/bin/rm x",
    "sudo -u root rm -rf x",
    "bash -c 'rm -rf x'",
    "echo $(rm -rf x)",
    "dd if=/dev/zero of=x",
    "git reset --hard HEAD",
    "git clean -fd",
    "git push --force origin main",
    "chmod 777 x",
    "systemctl restart x",
    "find . -delete",
    'echo "unterminated',
]


@pytest.mark.parametrize("command", ALLOWED_SHELL)
def test_ordinary_shell_command_runs_without_approval(command: str) -> None:
    assert verdict(ApprovalConfig(), "shell", command=command) == "allow"


@pytest.mark.parametrize("command", BLOCKED_SHELL)
def test_destructive_shell_command_waits_for_approval(command: str) -> None:
    assert verdict(ApprovalConfig(), "shell", command=command) == "pending"


def test_sensitive_tool_gates_every_call() -> None:
    assert verdict(ApprovalConfig(sensitive_tools=("shell",)), "shell", command="ls") == "pending"


def test_strict_mode_gates_everything_but_read_only() -> None:
    config = ApprovalConfig(mode="strict")
    assert verdict(config, "read", file="a.txt") == "allow"
    assert verdict(config, "shell", command="ls") == "pending"


def test_mode_off_bypasses_command_classifier() -> None:
    policy = build_safety(ApprovalConfig(mode="off"))
    assert policy.check(request("shell", command="rm -rf x")).verdict == "allow"


def test_destructive_internal_actions_wait_for_approval() -> None:
    config = ApprovalConfig()
    assert verdict(config, "memory", action="forget") == "pending"
    assert verdict(config, "memory", action="add", text="a") == "allow"
    assert verdict(config, "skill", action="archive", name="x") == "pending"
    assert verdict(config, "skill", action="save", name="x") == "allow"
    assert verdict(config, "recipe", action="remove", name="x") == "allow"


def test_empty_sensitive_commands_disables_command_gate() -> None:
    assert verdict(ApprovalConfig(sensitive_commands=()), "shell", command="rm -rf x") == "allow"
