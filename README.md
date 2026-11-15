# openai-image-mcp

A local [MCP](https://modelcontextprotocol.io) server that lets Claude Code and Claude Desktop generate and edit images with OpenAI's Image API, using your own OpenAI API key. Nothing is hosted online: each Claude app starts the server on your machine when it needs it, and the server talks only to `api.openai.com`.

## Tools

| Tool | What it does |
|---|---|
| `generate_image` | Creates 1 to 4 images from a text prompt. |
| `edit_image` | Edits local images, or combines several as references, with an optional mask. |

Both tools take `size` (`auto` or `WIDTHxHEIGHT`), `quality` (`low` to `max`), `background` (`auto`, `transparent`, `opaque`), `output_format` (`png`, `jpeg`, `webp`), `n`, and an optional `model`.

## Where images go

- **Saved to disk** at full resolution in `~/Pictures/Claude Images`, named `<date>_<time>_<prompt-words>.png`. Existing files are never overwritten.
- **Shown in the chat** as a downscaled JPEG preview (768 px on the long edge), with the saved paths. The preview keeps the reply under Claude Code's MCP output limit.

## Settings

Optional environment variables, set in each app's server entry:

| Variable | Default |
|---|---|
| `IMAGE_OUTPUT_DIR` | `~/Pictures/Claude Images` |
| `IMAGE_MODEL` | `gpt-image-2.6-flare` (fast). Use `gpt-image-2.6-sunburst` for detailed work and precise edits. |

## The API key

The key lives in Windows Credential Manager (service `openai-image-mcp`), never in a config file. The server reads it when a tool runs, so a changed key takes effect on the next call.

```powershell
uv run store_key.py                    # paste the key at a hidden prompt
uv run store_key.py --from-file PATH   # read it from a text file
uv run store_key.py --delete           # remove it
```

If no key is stored, the server falls back to the `OPENAI_API_KEY` environment variable.

Before your first image, complete **Organization Verification** in the OpenAI developer console; GPT Image models can refuse requests until it's done. A project-scoped key with a monthly spending limit is a good fit for this server.

## Setup

Requires [uv](https://docs.astral.sh/uv/).

1. `uv sync` in this folder.
2. Store the key (see above).
3. Register the server in Claude Code, available in every project:

   ```powershell
   claude mcp add --scope user --transport stdio openai-images -- "C:\Users\Roga\Documents\GitHub\openai-image-mcp\.venv\Scripts\openai-image-mcp.exe"
   ```

4. Register it in Claude Desktop. The Microsoft Store build keeps its config at `%LOCALAPPDATA%\Claude-Data\claude_pzs8sxrjxfjjc\claude_desktop_config.json` (other builds use `%APPDATA%\Claude\claude_desktop_config.json`; Settings, then Developer, then Edit Config opens the right one). Add:

   ```json
   "mcpServers": {
     "openai-images": {
       "command": "C:\\Users\\Roga\\Documents\\GitHub\\openai-image-mcp\\.venv\\Scripts\\openai-image-mcp.exe",
       "args": []
     }
   }
   ```

   Then quit Claude Desktop from the system tray and reopen it.

You never start the server yourself. Claude Desktop starts it when the app opens, and Claude Code starts it with each session.

## Testing

```powershell
uv run pytest
```

The tests never call OpenAI or read your stored key.

**Claude Code:** `claude mcp list` should show `openai-images: ✔ Connected`. In a new session, ask for "a low-quality 1024x1024 image of a lighthouse at dusk".

**Claude Desktop:** after a full restart, open **+**, then **Connectors**, then **Manage connectors**, and check that `openai-images` lists both tools. Ask for the same test image. Server logs are in the app's `logs` folder as `mcp-server-openai-images.log`.

## Branches and releases

Work happens on `dev`. Changes reach `main` only through a pull request from `dev`, merged with a merge commit once CI is green.

- **CI** (`.github/workflows/ci.yml`) runs the tests on Windows for every pull request into `main` and every push to `dev`, and fails a pull request into `main` that doesn't come from `dev`.
- **Release** (`.github/workflows/release.yml`) runs on every merge to `main`. It re-runs the tests, picks the next version from the commits since the last `vX.Y.Z` tag, and publishes a GitHub release with grouped notes.

| Change | How to get it |
|---|---|
| Patch (`1.2.3` to `1.2.4`) | The default: `fix:`, `docs:`, `chore:`, and any other commits. |
| Minor (`1.2.3` to `1.3.0`) | At least one `feat:` commit since the last release. |
| Major (`1.2.3` to `2.0.0`) | Add the `release:major` label to the `dev`-to-`main` pull request before merging. Nothing else bumps major, including a `!` or `BREAKING CHANGE` in a commit. |

The tag is the version of record, so `pyproject.toml` keeps its seed version and nothing is committed back to `main`. A failed release can be re-run from the Actions tab; a commit that's already tagged publishes nothing.

## Cost and speed

Each image is billed to your OpenAI account; GPT-Image-2.6 charges about $30 per million output tokens, so a single image costs cents to tens of cents depending on size and quality. Complex prompts can take up to two minutes. Use `quality: "low"` for drafts.
