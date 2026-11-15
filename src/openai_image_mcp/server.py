"""MCP server exposing OpenAI image generation to Claude Code and Claude Desktop.

Runs over stdio: each Claude app starts it on demand and stops it on exit.
"""

from datetime import datetime

import openai
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from openai_image_mcp import images, models
from openai_image_mcp.key import SETUP_HINT, MissingKeyError, load_api_key, make_client

# Generation can take up to about two minutes for complex prompts.
OPENAI_TIMEOUT_SECONDS = 300

server = MCPServer(
    name="openai-images",
    instructions=(
        "Generates and edits images with OpenAI's Image API. Every image is saved to the "
        "user's image folder at full resolution; the chat receives a smaller preview plus the "
        "saved path. Use quality 'low' for quick drafts. Each call costs the user money, so "
        "don't generate more images than asked for.\n\n"
        "Revisions: when the user asks to change an image already made or shared in this "
        "conversation (\"make the cat black\", \"now add sunglasses\"), call edit_image with the "
        "saved path of the most recent version and a prompt that states only the change plus what "
        "must stay the same. Don't call generate_image again, which draws a new, different image. "
        "Each revision is saved as a new numbered version (-v2, -v3, ...); to undo, edit an earlier "
        "version's path. Use generate_image only for a new image or when the user asks to start over.\n\n"
        "By default the newest image model is used; "
        "list_image_models shows what the user's key can use. To change their saved default, the "
        "user runs `uv run manage.py model <id>` themselves."
    ),
)

resolver = models.ModelResolver()


def _client() -> openai.OpenAI:
    try:
        key = load_api_key()
    except MissingKeyError as err:
        raise ToolError(str(err)) from err
    return make_client(key, timeout=OPENAI_TIMEOUT_SECONDS)


def _to_content(saved: list[images.SavedImage], model: str, opened: bool) -> list[str | Image]:
    lines = [f"Saved {len(saved)} image(s) with {model}:"]
    for item in saved:
        lines.append(f"- {item.path}")
        lines.append(f"  Open: {item.path.as_uri()}")
    lines.append(f"Folder: {saved[0].path.parent.as_uri()}")
    if opened:
        lines.append("Opened in the user's default image viewer.")
    lines.append(
        "The previews below are downscaled; the files above are full resolution. When you reply, "
        "include each Open link as-is so the user can click it (a terminal can't show the image)."
    )
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


def _run_image_call(call, requested_model: str | None, open_after: bool) -> list[str | Image]:
    client = _client()
    model = resolver.resolve(client, requested_model)
    try:
        saved = call(client, model)
        if open_after:
            for item in saved:
                images.open_in_viewer(item.path)
        return _to_content(saved, model, open_after)
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
    open_after: bool = False,
) -> list[str | Image]:
    """Generates new images from a text prompt and saves them to the user's image folder.

    For changes to an image that already exists, use edit_image on its saved path instead;
    generating again produces a different image rather than a revision.

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
        open_after: Open each saved image in the user's default image viewer. Set it when the
            user asks to see or open the result, which helps most in Claude Code, where the chat
            is a terminal that can't display images.
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
        open_after,
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
    open_after: bool = False,
    input_fidelity: images.InputFidelity = "auto",
) -> list[str | Image]:
    """Revises an existing image, or combines several as references, and saves the result.

    This is the tool for follow-up changes ("make it black", "add sunglasses", "remove the
    background"): pass the saved path of the latest version from the previous result. The
    result is saved as the next numbered version, so earlier versions stay available.

    Args:
        prompt: The change to make, and what must stay the same, for example "Make the cat's
            fur black. Keep the pose, background, and lighting unchanged." For several
            references, describe the new image to build from them.
        image_paths: Full paths to one or more local PNG, JPEG, or WebP files. For a revision,
            the single most recent version.
        mask_path: Optional PNG whose transparent areas mark where to edit the first image.
        size: "auto" or WIDTHxHEIGHT, with the same rules as generate_image.
        quality: "low" for fast drafts up to "max".
        background: "transparent" needs output_format "png" or "webp".
        output_format: File format of the saved image.
        n: Number of results, 1 to 4. Each one is billed.
        model: A model ID for this request only. Leave empty to use the user's default.
        open_after: Open each saved image in the user's default image viewer. Set it when the
            user asks to see or open the result, which helps most in Claude Code, where the chat
            is a terminal that can't display images.
        input_fidelity: Leave as "auto". GPT Image 2 and later always preserve input detail and
            reject this option; "high" helps only older models keep faces and fine detail.
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
            input_fidelity=input_fidelity,
        ),
        model,
        open_after,
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


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
