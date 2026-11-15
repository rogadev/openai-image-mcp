"""The user's saved choices (default model, output folder), shared by both Claude apps.

Stored as JSON in %APPDATA%\\openai-image-mcp\\settings.json on Windows, or
~/.config/openai-image-mcp/settings.json elsewhere. Only `manage.py` writes it;
the server only reads it, so nothing Claude does can change these choices.
"""

import json
import os
from pathlib import Path
from typing import Any


def settings_path() -> Path:
    if configured := os.environ.get("OPENAI_IMAGE_MCP_SETTINGS"):
        return Path(configured)
    base = os.environ.get("APPDATA") if os.name == "nt" else None
    root = Path(base) if base else Path.home() / ".config"
    return root / "openai-image-mcp" / "settings.json"


def read_settings() -> dict[str, Any]:
    try:
        data = json.loads(settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(settings: dict[str, Any]) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def save_setting(name: str, value: Any) -> None:
    settings = read_settings()
    settings[name] = value
    _write(settings)


def clear_setting(name: str) -> None:
    settings = read_settings()
    if settings.pop(name, None) is not None:
        _write(settings)


def known_folder(name: str) -> Path:
    """The user's real Desktop or Pictures folder, even when Windows has moved it (for
    example, into OneDrive)."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes

        csidl = {"desktop": 0x0010, "pictures": 0x0027}[name]  # CSIDL_DESKTOPDIRECTORY, CSIDL_MYPICTURES
        buffer = ctypes.create_unicode_buffer(wintypes.MAX_PATH)
        if ctypes.windll.shell32.SHGetFolderPathW(None, csidl, None, 0, buffer) == 0 and buffer.value:
            return Path(buffer.value)
    return Path.home() / {"desktop": "Desktop", "pictures": "Pictures"}[name]
