"""Finds the image models a key can use, picks the latest, and remembers a pinned choice.

The default model setting is "latest": each time the server needs a model it
asks OpenAI which image models the key can see and uses the newest, so a new
release is picked up without changing anything. Pinning a model ID instead
keeps using that exact model until the pin is changed.

Where the setting comes from, highest priority first:
1. The IMAGE_MODEL environment variable in an app's server entry.
2. The settings file written by `manage.py model`.
3. "latest".
"""

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

LATEST = "latest"
# Used only when OpenAI can't list models (for example, a restricted key).
FALLBACK_MODEL = "gpt-image-2.6-flare"
# Among variants of the newest version, the everyday model OpenAI recommends first.
PREFERRED_VARIANTS = ("flare",)

IMAGE_MODEL_PREFIXES = ("gpt-image", "chatgpt-image", "dall-e")
_VERSION = re.compile(r"^(?:gpt-image|chatgpt-image|dall-e)-(\d+)(?:\.(\d+))?")
_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")

CACHE_SECONDS = 3600


@dataclass(frozen=True)
class ImageModel:
    id: str
    created: int

    @property
    def is_snapshot(self) -> bool:
        """A dated ID such as gpt-image-2-2026-04-01 is a fixed snapshot, not a moving alias."""
        return bool(_SNAPSHOT.search(self.id))

    @property
    def version(self) -> tuple[int, int, int]:
        """(family, major, minor). Version numbers only compare within a family, and
        every GPT Image model is newer than every DALL-E model."""
        family = 0 if self.id.startswith("dall-e") else 1
        match = _VERSION.match(self.id)
        return (family, int(match[1]), int(match[2] or 0)) if match else (family, 0, 0)


def is_image_model(model_id: str) -> bool:
    return model_id.startswith(IMAGE_MODEL_PREFIXES)


def newest_first(models: list[ImageModel]) -> list[ImageModel]:
    return sorted(models, key=lambda m: (m.version, m.created, m.id), reverse=True)


def pick_latest(models: list[ImageModel]) -> str | None:
    """The newest image model, preferring a moving alias and the everyday variant."""
    candidates = [m for m in models if not m.is_snapshot] or list(models)
    if not candidates:
        return None
    top_version = max(m.version for m in candidates)
    newest = [m for m in candidates if m.version == top_version]
    for variant in PREFERRED_VARIANTS:
        for model in newest_first(newest):
            if model.id.endswith(f"-{variant}"):
                return model.id
    return newest_first(newest)[0].id


def fetch_image_models(client: Any) -> list[ImageModel]:
    """Every image model the key can use, newest first."""
    found = [
        ImageModel(id=m.id, created=int(getattr(m, "created", 0) or 0))
        for m in client.models.list()
        if is_image_model(m.id)
    ]
    return newest_first(found)


def search(models: list[ImageModel], query: str | None) -> list[ImageModel]:
    if not query:
        return models
    terms = query.lower().split()
    return [m for m in models if all(term in m.id.lower() for term in terms)]


def settings_path() -> Path:
    if configured := os.environ.get("OPENAI_IMAGE_MCP_SETTINGS"):
        return Path(configured)
    base = os.environ.get("APPDATA") if os.name == "nt" else None
    root = Path(base) if base else Path.home() / ".config"
    return root / "openai-image-mcp" / "settings.json"


def read_settings() -> dict:
    try:
        return json.loads(settings_path().read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def saved_default() -> str:
    return read_settings().get("default_model") or LATEST


def save_default(model: str) -> None:
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    settings = read_settings()
    settings["default_model"] = model
    path.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def configured_default() -> tuple[str, str]:
    """Returns (setting, where it came from)."""
    if env := os.environ.get("IMAGE_MODEL"):
        return env, "the IMAGE_MODEL environment variable"
    saved = saved_default()
    return saved, "the settings file" if saved != LATEST else "the built-in default"


class ModelResolver:
    """Turns the "latest" setting into a model ID, caching OpenAI's model list for an hour."""

    def __init__(self, clock=time.monotonic):
        self._clock = clock
        self._cached: list[ImageModel] | None = None
        self._cached_at = 0.0

    def available(self, client: Any, refresh: bool = False) -> list[ImageModel]:
        expired = self._clock() - self._cached_at > CACHE_SECONDS
        if refresh or self._cached is None or expired:
            self._cached = fetch_image_models(client)
            self._cached_at = self._clock()
        return self._cached

    def resolve(self, client: Any, requested: str | None = None) -> str:
        setting = requested or configured_default()[0]
        if setting != LATEST:
            return setting
        try:
            return pick_latest(self.available(client)) or FALLBACK_MODEL
        except Exception:  # noqa: BLE001 - listing is a convenience; generation must still work
            return FALLBACK_MODEL
