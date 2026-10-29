"""Kontrak tool sandbox: argv per runtime, deteksi runtime, timeout, batas output, env."""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from sanka.engine.config import ConfigError, SandboxConfig, load_config
from sanka.engine.tools import sandbox


class FakeProc:
    def __init__(self, out: bytes = b"", code: int = 0, hang: bool = False) -> None:
        self.out = out
        self.returncode = code
        self.hang = hang
        self.killed = False

    async def communicate(self) -> tuple[bytes, None]:
        if self.hang:
            await asyncio.sleep(60)
        return self.out, None

    def kill(self) -> None:
        self.killed = True

    async def wait(self) -> int:
        return self.returncode


def make_ctx(tmp_path: Path, **cfg) -> SimpleNamespace:
    workspace = tmp_path / "work"
    workspace.mkdir(exist_ok=True)
    return SimpleNamespace(
        config=SimpleNamespace(workspace=workspace, sandbox=SandboxConfig(**cfg))
    )


def fake_runtimes(monkeypatch, installed: set[str]) -> None:
    def which(name: str) -> str | None:
        return f"/usr/bin/{name}" if name in installed else None

    monkeypatch.setattr(sandbox.shutil, "which", which)


def capture(monkeypatch, proc: FakeProc) -> list[tuple[tuple, dict]]:
    calls: list[tuple[tuple, dict]] = []

    async def fake_exec(*argv, **kwargs):
        calls.append((argv, kwargs))
        return proc

    monkeypatch.setattr(sandbox.asyncio, "create_subprocess_exec", fake_exec)
    return calls


def run(ctx: SimpleNamespace, **args) -> str:
    payload = {"command": "ls /work", **args}
    return asyncio.run(sandbox._sandbox(payload, ctx))


def test_podman_argv_has_keep_id_and_selinux_suffix(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"podman"})
    monkeypatch.setenv("TZ", "Asia/Jakarta")
    calls = capture(monkeypatch, FakeProc())
    ctx = make_ctx(tmp_path, runtime="podman")

    result = run(ctx)

    argv = calls[0][0]
    mount = argv[argv.index("-v") + 1]
    assert "--userns=keep-id" in argv
    assert "--user" not in argv
    assert mount == f"{ctx.config.workspace}:/work:Z"
    assert argv[argv.index("--network") + 1] == "none"
    assert "--read-only" in argv and argv[argv.index("--tmpfs") + 1] == "/tmp"
    assert ["--env", "TZ=Asia/Jakarta"] == list(argv[argv.index("--env") : argv.index("--env") + 2])
    assert result.startswith("exit=0")


def test_docker_argv_drops_podman_flags_and_uses_host_workspace(
    tmp_path: Path, monkeypatch
) -> None:
    fake_runtimes(monkeypatch, {"docker"})
    calls = capture(monkeypatch, FakeProc())
    # Path host tidak terlihat dari container bot; tool tidak boleh menolaknya.
    host_ws = tmp_path / "host-workspace"
    assert not host_ws.exists()
    ctx = make_ctx(
        tmp_path, runtime="docker", host_workspace=host_ws, network="bridge", env=("NO_PROXY",)
    )
    monkeypatch.setenv("NO_PROXY", "localhost")

    run(ctx)

    argv, _ = calls[0]
    mount = argv[argv.index("-v") + 1]
    assert "--userns=keep-id" not in argv
    assert argv[argv.index("--user") + 1] == f"{os.getuid()}:{os.getgid()}"
    assert not mount.endswith(":Z")
    assert mount == f"{host_ws}:/work"
    assert argv[argv.index("--network") + 1] == "bridge"
    pairs = [argv[index + 1] for index, item in enumerate(argv) if item == "--env"]
    assert "NO_PROXY=localhost" in pairs


def test_docker_socket_permission_denied_is_actionable(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"docker"})
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "secret-xyz")
    denied = (
        b"permission denied while trying to connect to the Docker daemon socket "
        b"at unix:///var/run/docker.sock"
    )
    capture(monkeypatch, FakeProc(out=denied, code=125))

    result = run(make_ctx(tmp_path, runtime="docker"))

    assert not result.startswith("exit=")
    assert "UID container" in result or "uid container" in result
    assert "DOCKER_GID" in result
    assert "stat -c '%g %a' /var/run/docker.sock" in result
    assert "secret-xyz" not in result


def test_runtime_autodetect_and_missing_runtime_error(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"docker"})
    calls = capture(monkeypatch, FakeProc())
    run(make_ctx(tmp_path, runtime=""))
    assert calls[0][0][0] == "docker"

    fake_runtimes(monkeypatch, set())
    calls.clear()
    result = run(make_ctx(tmp_path, runtime=""))
    assert calls == []
    assert result.startswith("error:")
    assert "podman atau docker" in result


def test_missing_explicit_runtime_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"podman"})
    calls = capture(monkeypatch, FakeProc())

    result = run(make_ctx(tmp_path, runtime="docker"))

    assert calls == []
    assert "docker" in result and "config.yaml" in result


def test_timeout_kills_process(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"podman"})
    proc = FakeProc(hang=True)
    capture(monkeypatch, proc)

    result = run(make_ctx(tmp_path, runtime="podman"), timeout=1)

    assert result == "error: timeout setelah 1 detik"
    assert proc.killed is True


def test_output_is_tailed_and_marked(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"podman"})
    capture(monkeypatch, FakeProc(out=b"x" * 70000 + b"TAIL"))

    result = run(make_ctx(tmp_path, runtime="podman"))

    body = result.split("\n", 1)[1]
    assert body.startswith("(dipotong)")
    assert body.endswith("TAIL")
    assert len(body.encode()) <= sandbox.MAX_OUTPUT_BYTES + len("(dipotong)\n")


def test_container_env_does_not_inherit_secrets(tmp_path: Path, monkeypatch) -> None:
    fake_runtimes(monkeypatch, {"podman"})
    calls = capture(monkeypatch, FakeProc())
    monkeypatch.setenv("OPENCODE_GO_API_KEY", "secret-xyz")
    monkeypatch.setenv("TZ", "Asia/Jakarta")
    monkeypatch.setenv("LANG", "C.UTF-8")
    ctx = make_ctx(tmp_path, runtime="podman")

    run(ctx)

    argv, kwargs = calls[0]
    joined = " ".join(argv)
    assert "secret-xyz" not in joined
    assert "OPENCODE_GO_API_KEY" not in joined
    assert "secret-xyz" not in kwargs["env"].values()
    assert "OPENCODE_GO_API_KEY" not in kwargs["env"]
    forwarded = [
        argv[index + 1].split("=", 1)[0] for index, item in enumerate(argv) if item == "--env"
    ]
    assert set(forwarded) <= {"TZ", "LANG"}


def test_load_config_reads_host_workspace_and_rejects_file(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("SANKA_TEST_KEY", "x")
    host_ws = tmp_path / "host-workspace"
    host_ws.mkdir()
    target = tmp_path / "config.yaml"
    target.write_text(
        f"""
database:
  url: "sqlite+pysqlite://"
workspace: ".sanka/workspace"
sandbox:
  runtime: ""
  host_workspace: "{host_ws}"
  network: bridge
  env: ["NO_PROXY"]
""",
        encoding="utf-8",
    )
    (tmp_path / "engine.yaml").write_text(
        "llm:\n  api_key_env: SANKA_TEST_KEY\ndefault_role: owner\n", encoding="utf-8"
    )
    (tmp_path / "roles").mkdir()
    (tmp_path / "roles" / "owner.yaml").write_text(
        'name: owner\ntools: ["*"]\n', encoding="utf-8"
    )

    loaded = load_config(str(target))

    assert loaded.sandbox.runtime == ""
    assert loaded.sandbox.host_workspace == host_ws
    assert loaded.sandbox.network == "bridge"
    assert loaded.sandbox.env == ("NO_PROXY",)

    (tmp_path / "not-a-dir").write_text("x")
    target.write_text(
        target.read_text(encoding="utf-8").replace(str(host_ws), str(tmp_path / "not-a-dir")),
        encoding="utf-8",
    )
    with pytest.raises(ConfigError):
        load_config(str(target))
