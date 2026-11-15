"""MCP server exposing OpenAI image generation to Claude Code and Claude Desktop.

Runs over stdio: each Claude app starts it on demand and stops it on exit.
"""

from datetime import datetime

import openai
from mcp.server.mcpserver import Image, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from openai_image_mcp import images
from openai_image_mcp.key import MissingKeyError, load_api_key

# Generation can take up to about two minutes for complex prompts.
OPENAI_TIMEOUT_SECONDS = 300

server = MCPServer(
    name="openai-images",
    instructions=(
        "Generates and edits images with OpenAI's Image API. Every image is saved to the "
        "user's image folder at full resolution; the chat receives a smaller preview plus the "
        "saved path. Use quality 'low' for quick drafts. Each call costs the user money, so "
        "don't generate more images than asked for."
    ),
)


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


def _run(call, model: str) -> list[str | Image]:
    try:
        return _to_content(call(), model)
    except images.ImageRequestError as err:
        raise ToolError(str(err)) from err
    except openai.AuthenticationError as err:
        raise ToolError(
            "OpenAI rejected the API key. Store a valid key with `uv run store_key.py` in the "
            "openai-image-mcp folder, then restart the app."
        ) from err
    except openai.APIError as err:
        raise ToolError(f"OpenAI returned an error: {err}") from err


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
        model: Override the model, for example "gpt-image-2.6-sunburst" for detailed work.
    """
    chosen = model or images.default_model()
    return _run(
        lambda: images.generate(
            _client(),
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
        chosen,
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
        model: Override the model; "gpt-image-2.6-sunburst" is tuned for precise edits.
    """
    chosen = model or images.default_model()
    return _run(
        lambda: images.edit(
            _client(),
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
        chosen,
    )


def main() -> None:
    server.run("stdio")


if __name__ == "__main__":
    main()
