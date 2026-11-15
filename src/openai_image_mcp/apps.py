"""Registers and unregisters the server with Claude Code and Claude Desktop."""

import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

SERVER_NAME = "openai-images"
CONFIG_NAME = "claude_desktop_config.json"
BACKUP_SUFFIX = ".bak-openai-image-mcp"


def server_executable() -> Path:
    """The server program inside this project's virtual environment."""
    scripts = Path(sys.prefix) / ("Scripts" if os.name == "nt" else "bin")
    return scripts / ("openai-image-mcp.exe" if os.name == "nt" else "openai-image-mcp")


def desktop_config_paths(env: dict[str, str] | None = None, home: Path | None = None) -> list[Path]:
    """Finds every Claude Desktop config file on this machine.

    Claude Desktop keeps its config in different places depending on how it was
    installed: the standard installer, the Microsoft Store (two possible
    locations), macOS, or Linux.
    """
    env = os.environ if env is None else env
    home = Path.home() if home is None else home
    candidates: list[Path] = []
    if appdata := env.get("APPDATA"):
        candidates.append(Path(appdata) / "Claude" / CONFIG_NAME)
    if local := env.get("LOCALAPPDATA"):
        local_path = Path(local)
        candidates += sorted((local_path / "Claude-Data").glob(f"*/{CONFIG_NAME}"))
        candidates += sorted((local_path / "Packages").glob(f"Claude_*/LocalCache/Roaming/Claude/{CONFIG_NAME}"))
    candidates.append(home / "Library" / "Application Support" / "Claude" / CONFIG_NAME)
    candidates.append(home / ".config" / "Claude" / CONFIG_NAME)
    seen: set[Path] = set()
    found = []
    for path in candidates:
        if path.is_file() and path not in seen:
            seen.add(path)
            found.append(path)
    return found


def _read_config(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text) if text.strip() else {}


def _write_config(path: Path, config: dict) -> None:
    shutil.copy2(path, path.with_name(path.name + BACKUP_SUFFIX))
    path.write_text(json.dumps(config, indent=2) + "\n", encoding="utf-8")


def add_to_desktop_config(path: Path, executable: Path) -> bool:
    """Adds or updates the server entry. Returns whether the file changed."""
    config = _read_config(path)
    entry = {"command": str(executable), "args": []}
    servers = config.setdefault("mcpServers", {})
    existing = servers.get(SERVER_NAME)
    if existing and existing.get("command") == entry["command"]:
        return False
    if existing and "env" in existing:
        entry["env"] = existing["env"]
    servers[SERVER_NAME] = entry
    _write_config(path, config)
    return True


def remove_from_desktop_config(path: Path) -> bool:
    """Removes the server entry. Returns whether the file changed."""
    config = _read_config(path)
    servers = config.get("mcpServers")
    if not isinstance(servers, dict) or SERVER_NAME not in servers:
        return False
    del servers[SERVER_NAME]
    if not servers:
        del config["mcpServers"]
    _write_config(path, config)
    return True


@dataclass(frozen=True)
class CommandResult:
    ok: bool
    output: str


def _claude(*args: str) -> CommandResult | None:
    """Runs the Claude Code CLI, or returns None when it isn't installed."""
    exe = shutil.which("claude")
    if not exe:
        return None
    proc = subprocess.run([exe, *args], capture_output=True, text=True, encoding="utf-8", errors="replace")
    return CommandResult(proc.returncode == 0, (proc.stdout + proc.stderr).strip())


def claude_code_registered() -> bool | None:
    result = _claude("mcp", "get", SERVER_NAME)
    return None if result is None else result.ok


def add_to_claude_code(executable: Path) -> CommandResult | None:
    if claude_code_registered():
        _claude("mcp", "remove", SERVER_NAME, "-s", "user")
    return _claude("mcp", "add", "--scope", "user", "--transport", "stdio", SERVER_NAME, "--", str(executable))


def remove_from_claude_code() -> CommandResult | None:
    registered = claude_code_registered()
    if registered is None:
        return None
    if not registered:
        return CommandResult(True, "not registered")
    return _claude("mcp", "remove", SERVER_NAME, "-s", "user")
