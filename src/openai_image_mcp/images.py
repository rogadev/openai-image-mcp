"""Calls OpenAI's Image API, saves the results, and builds chat previews."""

import base64
import io
import os
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from PIL import Image as PILImage

from openai_image_mcp import settings

OUTPUT_SUBFOLDER = "Claude Images"

# Longest edge of the preview sent back into the chat. The full-resolution file
# is always saved to disk; the preview only has to be big enough for Claude to
# judge the result, and small enough to stay well under Claude Code's MCP
# output limit (25,000 tokens by default).
PREVIEW_MAX_EDGE = 768
PREVIEW_JPEG_QUALITY = 80

Quality = Literal["auto", "low", "medium", "high", "xhigh", "max"]
Background = Literal["auto", "transparent", "opaque"]
InputFidelity = Literal["auto", "low", "high"]
OutputFormat = Literal["png", "jpeg", "webp"]

MAX_IMAGES_PER_CALL = 4


class ImageRequestError(ValueError):
    """Raised for a request the server rejects before calling OpenAI."""


# What edit_image may upload. Pillow format name -> (file extensions, MIME type).
SOURCE_FORMATS = {
    "PNG": ((".png",), "image/png"),
    "JPEG": ((".jpg", ".jpeg"), "image/jpeg"),
    "WEBP": ((".webp",), "image/webp"),
}
MASK_FORMATS = {"PNG": SOURCE_FORMATS["PNG"]}
MAX_SOURCE_BYTES = 50 * 1024 * 1024


def load_source_image(raw_path: str, formats: dict[str, tuple[tuple[str, ...], str]]) -> tuple[str, bytes, str]:
    """Reads a local file for upload, but only if it really is an allowed image.

    edit_image uploads files to OpenAI, so a path that a misled Claude passes in
    (a key file, a document) must never leave the machine. The extension, the
    size, and the decoded content all have to agree that it's an image, and the
    bytes that were checked are the bytes that get sent.

    Returns:
        A (filename, bytes, MIME type) tuple, the form the OpenAI SDK uploads.
    """
    allowed = ", ".join(ext for exts, _ in formats.values() for ext in exts)
    path = Path(raw_path).expanduser()
    if not path.is_file():
        raise ImageRequestError(f"{raw_path} isn't a file.")
    if path.suffix.lower() not in {ext for exts, _ in formats.values() for ext in exts}:
        raise ImageRequestError(f"{raw_path} isn't an allowed image type ({allowed}).")
    if path.stat().st_size > MAX_SOURCE_BYTES:
        raise ImageRequestError(f"{raw_path} is larger than {MAX_SOURCE_BYTES // (1024 * 1024)} MB.")
    data = path.read_bytes()
    try:
        with PILImage.open(io.BytesIO(data)) as probe:
            detected = probe.format
            probe.verify()
    except Exception as err:  # noqa: BLE001 - any decode failure means "not an image"
        raise ImageRequestError(f"{raw_path} isn't a readable image.") from err
    if detected not in formats or path.suffix.lower() not in formats[detected][0]:
        raise ImageRequestError(f"{raw_path} isn't an allowed image type ({allowed}).")
    return path.name, data, formats[detected][1]


@dataclass(frozen=True)
class SavedImage:
    path: Path
    preview_jpeg: bytes


def default_output_dir() -> Path:
    return settings.known_folder("pictures") / OUTPUT_SUBFOLDER


def configured_output_dir() -> tuple[Path, str]:
    """Returns (folder, where the choice came from), highest priority first:
    the IMAGE_OUTPUT_DIR environment variable, the saved setting, then the default."""
    for value, source in (
        (os.environ.get("IMAGE_OUTPUT_DIR"), "the IMAGE_OUTPUT_DIR environment variable"),
        (settings.read_settings().get("output_dir"), "your saved setting"),
    ):
        if not value:
            continue
        # Only a full folder path counts; anything else (a relative path, or a value that
        # isn't text in a hand-edited settings file) falls back rather than guessing.
        folder = Path(value).expanduser() if isinstance(value, str) else None
        if folder is not None and folder.is_absolute():
            return folder, source
        return default_output_dir(), f"the built-in default, because {source} isn't a full folder path"
    return default_output_dir(), "the built-in default"


def output_dir() -> Path:
    """The folder images are saved to. Read on every request, so a change applies at once."""
    return configured_output_dir()[0]


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


def request_options(model: str, size: str, quality: str, background: str, output_format: str, n: int) -> dict[str, Any]:
    """Builds the Image API arguments, sending only the options that differ from OpenAI's defaults.

    Leaving defaults out keeps older or cheaper models working when they don't
    support a newer option. DALL-E models return a URL unless asked for base64.
    """
    options: dict[str, Any] = {"model": model}
    if size != "auto":
        options["size"] = size
    if quality != "auto":
        options["quality"] = quality
    if background != "auto":
        options["background"] = background
    if output_format != "png":
        options["output_format"] = output_format
    if n != 1:
        options["n"] = n
    if model.startswith("dall-e"):
        options["response_format"] = "b64_json"
    return options


def validate_options(n: int, background: str, output_format: str) -> None:
    if not 1 <= n <= MAX_IMAGES_PER_CALL:
        raise ImageRequestError(f"n must be between 1 and {MAX_IMAGES_PER_CALL}.")
    if background == "transparent" and output_format not in ("png", "webp"):
        raise ImageRequestError("A transparent background needs output_format 'png' or 'webp'.")


def slugify(prompt: str, max_words: int = 6) -> str:
    words = re.findall(r"[a-z0-9]+", prompt.lower())[:max_words]
    return "-".join(words) or "image"


_SAVED_NAME = re.compile(r"^\d{4}-\d{2}-\d{2}_\d{6}_(?P<base>.+?)(?:-v(?P<version>\d+))?$")


def revision_label(source_name: str) -> str:
    """Names an edit after the image it revises, numbering versions.

    An image this server saved as `<date>_<time>_a-cat.png` is version 1, so its
    edit becomes `a-cat-v2`, the edit of that becomes `a-cat-v3`, and so on. An
    image from anywhere else becomes `<its name>-edit`.
    """
    stem = Path(source_name).stem
    match = _SAVED_NAME.match(stem)
    if match:
        # The base comes from a file name, so reduce it to the same safe characters a
        # prompt slug uses, and keep both it and the counter to a sensible length.
        base = re.sub(r"[^a-z0-9]+", "-", match["base"].lower()).strip("-")[:80] or "image"
        version = min(int(match["version"] or 1), 9998)
        return f"{base}-v{version + 1}"
    return f"{slugify(stem)}-edit"


def unique_paths(folder: Path, label: str, extension: str, count: int, now: datetime) -> list[Path]:
    """Builds `<date>_<time>_<label>[-k].<ext>` paths that don't overwrite existing files."""
    stem = f"{now:%Y-%m-%d_%H%M%S}_{label}"
    paths: list[Path] = []
    suffix = 0
    while len(paths) < count:
        label = stem if count == 1 and suffix == 0 else f"{stem}-{suffix + 1}"
        candidate = folder / f"{label}.{extension}"
        if not candidate.exists() and candidate not in paths:
            paths.append(candidate)
        suffix += 1
    return paths


def open_in_viewer(path: Path) -> None:
    """Opens a file this server just saved in the system's default image viewer.

    Only ever called with paths save_results wrote, never with a path from the
    tool's arguments, so it can't be used to launch anything else.
    """
    if os.name == "nt":
        os.startfile(path)  # noqa: S606 - our own freshly written image file
    elif sys.platform == "darwin":
        subprocess.Popen(["open", str(path)])
    else:
        subprocess.Popen(["xdg-open", str(path)])


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


def save_results(response: Any, label: str, output_format: str, folder: Path, now: datetime) -> list[SavedImage]:
    """Writes each base64 image in an Images API response to disk."""
    payloads = [item.b64_json for item in (response.data or []) if getattr(item, "b64_json", None)]
    if not payloads:
        raise ImageRequestError("OpenAI returned no image data.")
    folder.mkdir(parents=True, exist_ok=True)
    extension = "jpg" if output_format == "jpeg" else output_format
    saved = []
    for path, payload in zip(unique_paths(folder, label, extension, len(payloads), now), payloads):
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
        prompt=prompt, **request_options(model, size, quality, background, output_format, n)
    )
    return save_results(response, slugify(prompt), output_format, folder, now)


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
    input_fidelity: str = "auto",
) -> list[SavedImage]:
    validate_size(size)
    validate_options(n, background, output_format)
    if not image_paths:
        raise ImageRequestError("Give at least one image path to edit.")
    sources = [load_source_image(p, SOURCE_FORMATS) for p in image_paths]
    mask = load_source_image(mask_path, MASK_FORMATS) if mask_path else None
    kwargs: dict[str, Any] = {
        "prompt": prompt,
        "image": sources if len(sources) > 1 else sources[0],
        **request_options(model, size, quality, background, output_format, n),
    }
    if mask:
        kwargs["mask"] = mask
    # GPT Image 2 and later always keep full detail from input images and reject this
    # option, so it's sent only when asked for, for older models that support it.
    if input_fidelity != "auto":
        kwargs["input_fidelity"] = input_fidelity
    response = client.images.edit(**kwargs)
    # One source image is a revision, named after it; several are a new composition.
    label = revision_label(Path(image_paths[0]).name) if len(image_paths) == 1 else slugify(prompt)
    return save_results(response, label, output_format, folder, now)
