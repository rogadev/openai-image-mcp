"""Talks to the real server over stdio, the same way Claude Code and Claude Desktop do."""

import os
import sys
from pathlib import Path

import anyio
import pytest
from mcp.client.session import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client

from openai_image_mcp import key

PROJECT_ROOT = Path(__file__).resolve().parents[1]


async def call_server(env: dict[str, str], tool: str | None = None, arguments: dict | None = None):
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "openai_image_mcp.server"],
        env=env,
        cwd=str(PROJECT_ROOT),
    )
    async with stdio_client(params) as (read, write):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = await session.list_tools()
            result = await session.call_tool(tool, arguments or {}) if tool else None
            return tools, result


def server_env(tmp_path, **extra) -> dict[str, str]:
    # PYTHON_KEYRING_BACKEND points keyring at a backend with no stored key, so
    # these tests never read the real key from Windows Credential Manager.
    env = {
        "PYTHON_KEYRING_BACKEND": "keyring.backends.fail.Keyring",
        "IMAGE_OUTPUT_DIR": str(tmp_path),
        "OPENAI_API_KEY": "",
        "IMAGE_MODEL": "gpt-image-test",
        "OPENAI_IMAGE_MCP_SETTINGS": str(tmp_path / "settings.json"),
    }
    env.update(extra)
    return env


def test_server_lists_all_tools(tmp_path):
    tools, _ = anyio.run(call_server, server_env(tmp_path))
    names = {t.name for t in tools.tools}
    assert names == {"generate_image", "edit_image", "list_image_models", "set_default_model"}


def test_missing_key_returns_setup_instructions(tmp_path):
    _, result = anyio.run(call_server, server_env(tmp_path), "generate_image", {"prompt": "a cat"})
    assert result.is_error
    assert "manage.py key" in result.content[0].text
    assert not any(tmp_path.iterdir())


def test_invalid_size_is_rejected_before_calling_openai(tmp_path):
    env = server_env(tmp_path, OPENAI_API_KEY="sk-test-not-real", OPENAI_BASE_URL="http://127.0.0.1:9")
    _, result = anyio.run(call_server, env, "generate_image", {"prompt": "a cat", "size": "1000x1000"})
    assert result.is_error
    assert "multiples of 16" in result.content[0].text


def test_load_api_key_falls_back_to_environment(monkeypatch):
    monkeypatch.setenv("PYTHON_KEYRING_BACKEND", "keyring.backends.fail.Keyring")
    import keyring.core

    monkeypatch.setattr(keyring.core, "_keyring_backend", None)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert key.load_api_key() == "sk-from-env"
    monkeypatch.setenv("OPENAI_API_KEY", "")
    with pytest.raises(key.MissingKeyError, match="manage.py key"):
        key.load_api_key()
