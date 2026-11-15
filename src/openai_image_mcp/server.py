"""MCP server exposing OpenAI image generation to Claude Code and Claude Desktop.

Runs over stdio: each Claude app starts it on demand and stops it on exit.
"""

import os
from datetime import datetime

import openai
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from openai_image_mcp import images, models
from openai_image_mcp.key import SETUP_HINT, MissingKeyError, load_api_key

# Generation can take up to about two minutes for complex prompts.
OPENAI_TIMEOUT_SECONDS = 300

server = MCPServer(
    name="openai-images",
    instructions=(
        "Generates and edits images with OpenAI's Image API. Every image is saved to the "
        "user's image folder at full resolution; the chat receives a smaller preview plus the "
        "saved path. Use quality 'low' for quick drafts. Each call costs the user money, so "
        "don't generate more images than asked for. By default the newest image model is used; "
        "list_image_models shows what the user's key can use, and set_default_model pins one."
    ),
)

resolver = models.ModelResolver()


def _client() -> openai.OpenAI:
    try:
        key = load_api_key()
    except MissingKeyError as err:
        raise ToolError(str(err)) from err
    return openai.OpenAI(api_key=key, timeout=OPENAI_TIMEOUT_SECONDS, max_retries=1)


def _to_content(saved: list[images.SavedImage], model: str) -> list[str | Image]:
    lines = [f"Saved {len(saved)} image(s) with {model}:"]
    lines += [f"- {item.path}" for item in saved]
    lines.append("The previews below are downscaled; the files above are full resolution.")
    content: list[str | Image] = ["\n".join(lines)]
    content += [Image(data=item.preview_jpeg, format="jpeg") for item in saved]
    return content


def _openai_errors(err: Exception) -> ToolError:
    if isinstance(err, openai.AuthenticationError):
        return ToolError(
            "OpenAI rejected the stored API key. It may have expired or been revoked. "
            f"To replace it, {SETUP_HINT}; the new key works on the next request, with no restart."
        )
    if isinstance(err, openai.NotFoundError):
        return ToolError(f"OpenAI doesn't know that model, or this key can't use it: {err}")
    return ToolError(f"OpenAI returned an error: {err}")


def _run_image_call(call, requested_model: str | None) -> list[str | Image]:
    client = _client()
    model = resolver.resolve(client, requested_model)
    try:
        return _to_content(call(client, model), model)
    except images.ImageRequestError as err:
        raise ToolError(str(err)) from err
    except openai.APIError as err:
        raise _openai_errors(err) from err


@server.tool(structured_output=False)
def generate_image(
    prompt: str,
    size: str = "auto",
    quality: images.Quality = "auto",
    background: images.Background = "auto",
    output_format: images.OutputFormat = "png",
    n: int = 1,
    model: str | None = None,
) -> list[str | Image]:
    """Generates images from a text prompt and saves them to the user's image folder.

    Args:
        prompt: What to draw. Be specific about subject, style, composition, and any text to render.
        size: "auto", or WIDTHxHEIGHT such as 1024x1024 (square), 1536x1024 (landscape), or
            1024x1536 (portrait). Edges must be multiples of 16, at most 3840.
        quality: "low" for fast drafts up to "max". "auto" lets the model choose.
        background: "transparent" needs output_format "png" or "webp".
        output_format: File format of the saved image.
        n: Number of images, 1 to 4. Each one is billed.
        model: A model ID for this request only. Leave empty to use the user's default
            (the newest model unless they pinned one). See list_image_models.
    """
    return _run_image_call(
        lambda client, chosen: images.generate(
            client,
            prompt,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            n=n,
            model=chosen,
            folder=images.output_dir(),
            now=datetime.now(),
        ),
        model,
    )


@server.tool(structured_output=False)
def edit_image(
    prompt: str,
    image_paths: list[str],
    mask_path: str | None = None,
    size: str = "auto",
    quality: images.Quality = "auto",
    background: images.Background = "auto",
    output_format: images.OutputFormat = "png",
    n: int = 1,
    model: str | None = None,
) -> list[str | Image]:
    """Edits existing local images, or combines them as references, and saves the result.

    Args:
        prompt: The change to make, or the new image to build from the references.
        image_paths: Full paths to one or more local PNG, JPEG, or WebP files.
        mask_path: Optional PNG whose transparent areas mark where to edit the first image.
        size: "auto" or WIDTHxHEIGHT, with the same rules as generate_image.
        quality: "low" for fast drafts up to "max".
        background: "transparent" needs output_format "png" or "webp".
        output_format: File format of the saved image.
        n: Number of results, 1 to 4. Each one is billed.
        model: A model ID for this request only. Leave empty to use the user's default.
    """
    return _run_image_call(
        lambda client, chosen: images.edit(
            client,
            prompt,
            image_paths,
            mask_path=mask_path,
            size=size,
            quality=quality,
            background=background,
            output_format=output_format,
            n=n,
            model=chosen,
            folder=images.output_dir(),
            now=datetime.now(),
        ),
        model,
    )


def _model_overview(client: openai.OpenAI, query: str | None) -> str:
    try:
        available = resolver.available(client, refresh=True)
    except openai.APIError as err:
        raise _openai_errors(err) from err
    setting, source = models.configured_default()
    latest = models.pick_latest(available)
    in_use = latest if setting == models.LATEST else setting
    matches = models.search(available, query)

    lines = [
        f"Default setting: {setting} (from {source}).",
        f"Model used when none is given: {in_use or models.FALLBACK_MODEL}.",
        f"Newest available: {latest or 'none found'}.",
        "",
        f"Image models this key can use{f' matching {query!r}' if query else ''}, newest first:",
    ]
    if not matches:
        lines.append("- none")
    for model in matches:
        tags = []
        if model.id == latest:
            tags.append("newest")
        if model.id == in_use:
            tags.append("current default")
        if model.is_snapshot:
            tags.append("fixed snapshot")
        lines.append(f"- {model.id}" + (f" ({', '.join(tags)})" if tags else ""))
    lines.append("")
    lines.append("Prices differ by model; see https://openai.com/api/pricing/ for current rates.")
    return "\n".join(lines)


@server.tool()
def list_image_models(query: str | None = None) -> str:
    """Lists the OpenAI image models the user's API key can use, newest first.

    Shows which model is used by default and whether the user pinned one.

    Args:
        query: Optional words to filter by, such as "2.5" or "flare".
    """
    return _model_overview(_client(), query)


@server.tool()
def set_default_model(model: str) -> str:
    """Sets the model used when a request doesn't name one, and remembers it.

    Only call this when the user asks to change or pin their default model.

    Args:
        model: "latest" to always use the newest model, or an exact model ID from
            list_image_models to pin that model.
    """
    client = _client()
    if model != models.LATEST:
        try:
            ids = {m.id for m in resolver.available(client, refresh=True)}
        except openai.APIError as err:
            raise _openai_errors(err) from err
        if model not in ids:
            raise ToolError(f"{model} isn't one of the image models this key can use. See list_image_models.")
    models.save_default(model)
    note = ""
    if os.environ.get("IMAGE_MODEL"):
        note = " Note: an IMAGE_MODEL environment variable is set for this app and takes priority."
    if model == models.LATEST:
        return "Default set to latest: each request uses the newest image model available." + note
    return f"Default pinned to {model}. It stays until you change it." + note


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
