"""Tests for sanka.secrets."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from sanka.secrets import SecretError, get_secret, load_secrets_file

posix_only = pytest.mark.skipif(os.name != "posix", reason="POSIX permission bits")


def write_secrets(path: Path, content: str, mode: int = 0o600) -> Path:
    """Write a throwaway secrets file with an explicit permission mode."""
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_environment_wins_over_file(tmp_path: Path) -> None:
    path = write_secrets(tmp_path / "secrets.env", "API_KEY=from-file\n")

    value = get_secret(
        "API_KEY",
        env={"API_KEY": "from-env"},
        secrets_path=path,
    )

    assert value == "from-env"


def test_file_is_used_when_environment_is_unset(tmp_path: Path) -> None:
    path = write_secrets(tmp_path / "secrets.env", "API_KEY=from-file\n")

    assert get_secret("API_KEY", env={}, secrets_path=path) == "from-file"


def test_empty_environment_value_falls_back_to_file(tmp_path: Path) -> None:
    path = write_secrets(tmp_path / "secrets.env", "API_KEY=from-file\n")

    assert get_secret("API_KEY", env={"API_KEY": ""}, secrets_path=path) == "from-file"


def test_empty_environment_value_without_file_is_missing(tmp_path: Path) -> None:
    path = tmp_path / "absent.env"

    with pytest.raises(SecretError):
        get_secret("API_KEY", env={"API_KEY": ""}, secrets_path=path)

    assert not path.exists()


def test_missing_secret_error_does_not_include_values(tmp_path: Path) -> None:
    path = write_secrets(
        tmp_path / "secrets.env",
        "OTHER_TOKEN=very-secret-value\n",
    )

    with pytest.raises(SecretError) as excinfo:
        get_secret("ABSENT_TOKEN", env={}, secrets_path=path)

    message = str(excinfo.value)
    assert "missing secret: ABSENT_TOKEN" in message
    assert "very-secret-value" not in message


@posix_only
def test_0600_file_is_accepted(tmp_path: Path) -> None:
    path = write_secrets(tmp_path / "secrets.env", "API_KEY=value\n", mode=0o600)

    assert load_secrets_file(path) == {"API_KEY": "value"}


@posix_only
def test_0644_file_is_rejected(tmp_path: Path) -> None:
    path = write_secrets(tmp_path / "secrets.env", "API_KEY=value\n", mode=0o644)

    with pytest.raises(SecretError) as excinfo:
        load_secrets_file(path)

    message = str(excinfo.value)
    assert "permission" in message.lower()
    assert "0644" in message
    assert "value\n" not in message


@pytest.mark.parametrize(
    ("content", "line_number"),
    [
        ("# comment\nGOOD=ok\nBROKEN LINE\n", 3),
        ("GOOD=ok\n=orphan-value\n", 2),
    ],
)
def test_malformed_line_reports_line_number(
    tmp_path: Path,
    content: str,
    line_number: int,
) -> None:
    path = write_secrets(tmp_path / "secrets.env", content)

    with pytest.raises(SecretError) as excinfo:
        load_secrets_file(path)

    message = str(excinfo.value)
    assert f"line {line_number}" in message
    assert "orphan-value" not in message


def test_comments_blank_lines_and_whitespace_are_ignored(tmp_path: Path) -> None:
    content = "# top comment\n\n   \n  API_KEY  =  value-one  \n#DISABLED=two\nTOKEN=value-two\n"
    path = write_secrets(tmp_path / "secrets.env", content)

    assert load_secrets_file(path) == {
        "API_KEY": "value-one",
        "TOKEN": "value-two",
    }


def test_missing_file_raises_secret_error(tmp_path: Path) -> None:
    with pytest.raises(SecretError):
        load_secrets_file(tmp_path / "absent.env")


def test_get_secret_falls_back_to_dotenv(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_secrets(tmp_path / ".env", "API_KEY=from-dotenv\n")
    monkeypatch.chdir(tmp_path)

    assert get_secret("API_KEY", env={}) == "from-dotenv"
