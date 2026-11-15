"""Calls OpenAI's Image API, saves the results, and builds chat previews."""

import base64
import io
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image as PILImage

DEFAULT_MODEL = "gpt-image-2.6-flare"
DEFAULT_OUTPUT_DIR = Path.home() / "Pictures" / "Claude Images"

# Longest edge of the preview sent back into the chat. The full-resolution file
# is always saved to disk; the preview only has to be big enough for Claude to
# judge the result, and small enough to stay well under Claude Code's MCP
# output limit (25,000 tokens by default).
PREVIEW_MAX_EDGE = 768
PREVIEW_JPEG_QUALITY = 80

Quality = Literal["auto", "low", "medium", "high", "xhigh", "max"]
Background = Literal["auto", "transparent", "opaque"]
OutputFormat = Literal["png", "jpeg", "webp"]

MAX_IMAGES_PER_CALL = 4


class ImageRequestError(ValueError):
    """Raised for a request the server rejects before calling OpenAI."""


@dataclass(frozen=True)
class SavedImage:
    path: Path
    preview_jpeg: bytes


def output_dir() -> Path:
    """Returns the folder images are saved to, from IMAGE_OUTPUT_DIR or the default."""
    configured = os.environ.get("IMAGE_OUTPUT_DIR")
    return Path(configured).expanduser() if configured else DEFAULT_OUTPUT_DIR


def default_model() -> str:
    return os.environ.get("IMAGE_MODEL") or DEFAULT_MODEL


def validate_size(size: str) -> str:
    """Checks a size string against OpenAI's rules for GPT-Image-2.5 and later.

    Accepts "auto" or "WIDTHxHEIGHT" where both edges are multiples of 16, no
    edge exceeds 3840, the aspect ratio is within 3:1, and the total pixel count
    is between 655,360 and 8,294,400.
    """
    if size == "auto":
        return size
    match = re.fullmatch(r"(\d+)x(\d+)", size)
    if not match:
        raise ImageRequestError(f"Size must be 'auto' or WIDTHxHEIGHT, such as 1024x1024. Got {size!r}.")
    width, height = int(match[1]), int(match[2])
    problems = []
    if width % 16 or height % 16:
        problems.append("width and height must be multiples of 16")
    if max(width, height) > 3840:
        problems.append("no edge may exceed 3840 px")
    if max(width, height) > 3 * min(width, height):
        problems.append("aspect ratio must be between 1:3 and 3:1")
    if not 655_360 <= width * height <= 8_294_400:
        problems.append("total pixels must be between 655,360 and 8,294,400")
    if problems:
        raise ImageRequestError(f"Size {size} is not allowed: {'; '.join(problems)}.")
    return size


def validate_options(n: int, background: str, output_format: str) -> None:
    if not 1 <= n <= MAX_IMAGES_PER_CALL:
        raise ImageRequestError(f"n must be between 1 and {MAX_IMAGES_PER_CALL}.")
    if background == "transparent" and output_format not in ("png", "webp"):
        raise ImageRequestError("A transparent background needs output_format 'png' or 'webp'.")


def slugify(prompt: str, max_words: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())[:max_words]
    return "-".join(words) or "image"


def unique_paths(folder: Path, prompt: str, extension: str, count: int, now: datetime) -> list[Path]:
    """Builds `<date>_<time>_<slug>[-k].<ext>` paths that don't overwrite existing files."""
    stem = f"{now:%Y-%m-%d_%H%M%S}_{slugify(prompt)}"
    paths: list[Path] = []
    suffix = 0
    while len(paths) < count:
        label = stem if count == 1 and suffix == 0 else f"{stem}-{suffix + 1}"
        candidate = folder / f"{label}.{extension}"
        if not candidate.exists() and candidate not in paths:
            paths.append(candidate)
        suffix += 1
    return paths


def make_preview(image_bytes: bytes) -> bytes:
    """Downscales an image to a JPEG preview, flattening transparency onto white."""
    with PILImage.open(io.BytesIO(image_bytes)) as img:
        img.thumbnail((PREVIEW_MAX_EDGE, PREVIEW_MAX_EDGE))
        if img.mode in ("RGBA", "LA", "P"):
            rgba = img.convert("RGBA")
            flat = PILImage.new("RGB", rgba.size, "white")
            flat.paste(rgba, mask=rgba.getchannel("A"))
            img = flat
        elif img.mode != "RGB":
            img = img.convert("RGB")
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=PREVIEW_JPEG_QUALITY)
        return out.getvalue()


def save_results(response: Any, prompt: str, output_format: str, folder: Path, now: datetime) -> list[SavedImage]:
    """Writes each base64 image in an Images API response to disk."""
    payloads = [item.b64_json for item in (response.data or []) if getattr(item, "b64_json", None)]
    if not payloads:
        raise ImageRequestError("OpenAI returned no image data.")
    folder.mkdir(parents=True, exist_ok=True)
    extension = "jpg" if output_format == "jpeg" else output_format
    saved = []
    for path, payload in zip(unique_paths(folder, prompt, extension, len(payloads), now), payloads):
        image_bytes = base64.b64decode(payload)
        path.write_bytes(image_bytes)
        saved.append(SavedImage(path=path, preview_jpeg=make_preview(image_bytes)))
    return saved


def generate(
    client: Any,
    prompt: str,
    *,
    size: str,
    quality: str,
    background: str,
    output_format: str,
    n: int,
    model: str,
    folder: Path,
    now: datetime,
) -> list[SavedImage]:
    validate_size(size)
    validate_options(n, background, output_format)
    response = client.images.generate(
        model=model,
        prompt=prompt,
        size=size,
        quality=quality,
        background=background,
        output_format=output_format,
        n=n,
    )
    return save_results(response, prompt, output_format, folder, now)


def edit(
    client: Any,
    prompt: str,
    image_paths: Sequence[str],
    *,
    mask_path: str | None,
    size: str,
    quality: str,
    background: str,
    output_format: str,
    n: int,
    model: str,
    folder: Path,
    now: datetime,
) -> list[SavedImage]:
    validate_size(size)
    validate_options(n, background, output_format)
    if not image_paths:
        raise ImageRequestError("Give at least one image path to edit.")
    sources = [Path(p).expanduser() for p in image_paths]
    missing = [str(p) for p in sources if not p.is_file()]
    if mask_path and not Path(mask_path).expanduser().is_file():
        missing.append(mask_path)
    if missing:
        raise ImageRequestError(f"These files don't exist: {', '.join(missing)}")

    handles = [p.open("rb") for p in sources]
    mask_handle = Path(mask_path).expanduser().open("rb") if mask_path else None
    try:
        kwargs: dict[str, Any] = {
            "model": model,
            "prompt": prompt,
            "image": handles if len(handles) > 1 else handles[0],
            "size": size,
            "quality": quality,
            "background": background,
            "output_format": output_format,
            "n": n,
        }
        if mask_handle:
            kwargs["mask"] = mask_handle
        response = client.images.edit(**kwargs)
    finally:
        for handle in handles:
            handle.close()
        if mask_handle:
            mask_handle.close()
    return save_results(response, prompt, output_format, folder, now)
