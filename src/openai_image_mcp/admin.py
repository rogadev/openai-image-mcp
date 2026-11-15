"""Command-line setup, key management, model choice, and uninstall. Run via manage.py."""

import argparse
import getpass
import sys
import uuid
from pathlib import Path
from collections.abc import Callable

import openai

from openai_image_mcp import apps, images, key, models, settings

Printer = Callable[[str], None]


def _resolve_check_model(api_key: str) -> str:
    setting = models.configured_default()[0]
    if setting != models.LATEST:
        return setting
    try:
        client = key.make_client(api_key)
        return models.pick_latest(models.fetch_image_models(client)) or models.FALLBACK_MODEL
    except openai.APIError:
        return models.FALLBACK_MODEL


def set_key(
    out: Printer = print,
    read_secret: Callable[[str], str] = getpass.getpass,
    check: bool = True,
    confirm: Callable[[str], str] = input,
) -> int:
    out("Paste your OpenAI API key and press Enter. It stays hidden while you type or paste.")
    new_key = read_secret("OpenAI API key: ").strip()
    if problem := key.key_problem(new_key):
        out(f"{problem} Nothing was saved.")
        return 1
    out(key.paste_summary(new_key))
    if check:
        # Checking sends the pasted text to OpenAI. If it doesn't look like an OpenAI key,
        # it may be some other secret pasted by mistake, so ask before it leaves the machine.
        if not new_key.startswith("sk-"):
            answer = confirm("Send it to OpenAI to check anyway? [y/N] ").strip().lower()
            if answer not in ("y", "yes"):
                out("Nothing was sent or saved.")
                return 1
        result = key.check_api_key(new_key, _resolve_check_model(new_key))
        out(result.message)
        if not result.key_valid:
            out("Nothing was saved.")
            if "rejected" in result.message:
                out(
                    "OpenAI shows a full key only once, when it's created; copy it from that pop-up at "
                    "https://platform.openai.com/api-keys (the key list shows only names and masked keys)."
                )
            else:
                out("To save it without checking, run `uv run manage.py key --no-check`.")
            return 1
    replacing = key.stored_key() is not None
    key.save_api_key(new_key)
    action = "Replaced the stored key with" if replacing else "Saved"
    out(f"{action} {key.describe_key(new_key)} in your system's credential store.")
    out("It takes effect on the next image request; no restart needed.")
    return 0


def check(out: Printer = print) -> int:
    stored = key.stored_key()
    status = 0
    if stored:
        out(f"Key: {key.describe_key(stored)} is stored.")
        model = _resolve_check_model(stored)
        result = key.check_api_key(stored, model)
        out(f"     {result.message}")
        status = 0 if result.ok else 1
    else:
        out("Key: none stored. Run `uv run manage.py key` to add one.")
        status = 1
    setting, source = models.configured_default()
    out(f"Default model: {setting} (from {source}).")
    folder, folder_source = images.configured_output_dir()
    out(f"Images folder: {folder} (from {folder_source}).")
    out(f"Trigger mode: {settings.trigger_mode()}.")
    registered = apps.claude_code_registered()
    out(
        "Claude Code: "
        + {None: "not installed (no `claude` command found).", True: "registered.", False: "not registered."}[registered]
    )
    configs = apps.desktop_config_paths()
    if not configs:
        out("Claude Desktop: no config file found.")
    for path in configs:
        has_entry = apps.SERVER_NAME in apps._read_config(path).get("mcpServers", {})
        out(f"Claude Desktop: {'registered' if has_entry else 'not registered'} in {path}")
    return status


def list_models(query: str | None, out: Printer = print) -> int:
    stored = key.load_api_key()
    client = key.make_client(stored)
    available = models.fetch_image_models(client)
    latest = models.pick_latest(available)
    setting, source = models.configured_default()
    out(f"Default setting: {setting} (from {source}). Newest available: {latest or 'none'}.")
    matches = models.search(available, query)
    if not matches:
        out("No image models match." if query else "This key can't see any image models.")
    for model in matches:
        tags = [t for t, on in (("newest", model.id == latest), ("pinned", model.id == setting)) if on]
        if model.is_snapshot:
            tags.append("fixed snapshot")
        out(f"  {model.id}" + (f"  ({', '.join(tags)})" if tags else ""))
    return 0


def set_model(model: str, out: Printer = print) -> int:
    if model != models.LATEST:
        client = key.make_client(key.load_api_key())
        ids = {m.id for m in models.fetch_image_models(client)}
        if model not in ids:
            out(f"{model} isn't an image model this key can use. Run `uv run manage.py models` to see them.")
            return 1
    models.save_default(model)
    if model == models.LATEST:
        out("Default set to latest: each request uses the newest image model available.")
    else:
        out(f"Default pinned to {model}. Run `uv run manage.py model latest` to go back to the newest.")
    return 0


def register(out: Printer = print) -> int:
    exe = apps.server_executable()
    if not exe.is_file():
        out(f"Can't find the server program at {exe}. Run `uv sync` first.")
        return 1
    result = apps.add_to_claude_code(exe)
    if result is None:
        out("Claude Code: not installed, skipped.")
    else:
        out("Claude Code: registered for all projects." if result.ok else f"Claude Code: failed. {result.output}")
    configs = apps.desktop_config_paths()
    if not configs:
        out(
            "Claude Desktop: no config file found. If you use Claude Desktop, open Settings, then Developer, "
            "then Edit Config once to create it, and run this again."
        )
    for path in configs:
        changed = apps.add_to_desktop_config(path, exe)
        out(f"Claude Desktop: {'registered' if changed else 'already registered'} in {path}")
    if configs:
        out("Quit Claude Desktop from the system tray and reopen it to load the server.")
    return 0


FOLDER_SHORTCUTS = ("desktop", "pictures")


def resolve_folder(choice: str) -> Path:
    """Turns "desktop", "pictures", or a path into an absolute folder path."""
    lowered = choice.strip().strip('"').lower()
    if lowered in FOLDER_SHORTCUTS:
        return settings.known_folder(lowered)
    return Path(choice.strip().strip('"')).expanduser().resolve()


def set_folder(choice: str | None, out: Printer = print) -> int:
    """Shows, sets, or resets the folder images are saved to."""
    if choice is None:
        folder, source = images.configured_output_dir()
        out(f"Images are saved to {folder} (from {source}).")
        return 0
    if choice.strip().lower() == "default":
        settings.clear_setting("output_dir")
        out(f"Images will be saved to the default folder, {images.default_output_dir()}.")
        return 0
    folder = resolve_folder(choice)
    try:
        folder.mkdir(parents=True, exist_ok=True)
        probe = folder / f".openai-image-mcp-{uuid.uuid4().hex}.tmp"
        probe.write_bytes(b"")
        probe.unlink()
    except OSError as err:
        out(f"Can't save images to {folder}: {err.strerror or err}. Nothing was changed.")
        return 1
    settings.save_setting("output_dir", str(folder))
    out(f"Images will be saved to {folder}, starting with the next image. No restart needed.")
    return 0


def set_trigger(mode: str | None, out: Printer = print) -> int:
    """Shows or sets whether Claude uses the tool on its own or only when asked by name."""
    current = settings.trigger_mode()
    if mode is None:
        out(f"Trigger mode: {current}. {TRIGGER_HELP[current]}")
        return 0
    mode = mode.strip().lower()
    if mode not in settings.TRIGGER_MODES:
        out("Choose 'implicit' or 'explicit'.")
        return 1
    settings.save_setting("trigger", mode)
    out(f"Trigger mode set to {mode}. {TRIGGER_HELP[mode]}")
    out("Restart Claude Desktop and start a new Claude Code session for this to take effect.")
    return 0


TRIGGER_HELP = {
    "implicit": "Claude uses OpenAI whenever an image would help.",
    "explicit": "Claude uses OpenAI only when you ask for an OpenAI image by name; other visuals use Claude's own abilities.",
}


def setup(
    out: Printer = print,
    read_secret: Callable[[str], str] = getpass.getpass,
    ask: Callable[[str], str] = input,
) -> int:
    if key.stored_key() is None:
        if set_key(out, read_secret) != 0:
            return 1
    else:
        out(f"Key: {key.describe_key(key.stored_key())} is already stored. Run `uv run manage.py key` to replace it.")
    current = images.output_dir()
    out(f"Images will be saved to {current}.")
    answer = ask("Press Enter to keep it, or type 'desktop', 'pictures', or a folder path: ").strip()
    if answer and set_folder(answer, out) != 0:
        return 1
    return register(out)


def uninstall(out: Printer = print, keep_key: bool = False) -> int:
    result = apps.remove_from_claude_code()
    if result is None:
        out("Claude Code: not installed, skipped.")
    else:
        out("Claude Code: removed." if result.ok else f"Claude Code: couldn't remove. {result.output}")
    for path in apps.desktop_config_paths():
        if apps.remove_from_desktop_config(path):
            out(f"Claude Desktop: removed from {path}")
    if keep_key:
        out("Key: kept, as asked.")
    else:
        out("Key: removed from your credential store." if key.delete_api_key() else "Key: none was stored.")
    settings_file = settings.settings_path()
    if settings_file.exists():
        settings_file.unlink()
        try:
            settings_file.parent.rmdir()
        except OSError:
            pass
        out(f"Settings: removed {settings_file}")
    out("")
    out("Left in place: your generated images (in your images folder) and this project folder.")
    out("To finish, quit Claude Desktop and close Claude Code sessions, then delete this folder.")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="manage.py", description="Set up and manage openai-image-mcp.")
    commands = parser.add_subparsers(dest="command", required=True, metavar="command")
    commands.add_parser("setup", help="store your key and add the server to Claude Code and Claude Desktop")
    key_cmd = commands.add_parser("key", help="add or replace your OpenAI API key")
    key_cmd.add_argument("--no-check", action="store_true", help="save without asking OpenAI whether it works")
    commands.add_parser("check", help="test the stored key and show what's installed")
    models_cmd = commands.add_parser("models", help="list the image models your key can use")
    models_cmd.add_argument("query", nargs="*", help="optional words to filter by, such as 2.5 or flare")
    model_cmd = commands.add_parser("model", help="choose the default model: 'latest' or an exact model ID")
    model_cmd.add_argument("model")
    folder_cmd = commands.add_parser(
        "folder", help="show or choose where images are saved: 'desktop', 'pictures', a path, or 'default'"
    )
    folder_cmd.add_argument("folder", nargs="?")
    trigger_cmd = commands.add_parser(
        "trigger", help="show or choose when Claude uses OpenAI: 'implicit' (on its own) or 'explicit' (only when asked)"
    )
    trigger_cmd.add_argument("mode", nargs="?")
    commands.add_parser("register", help="add the server to Claude Code and Claude Desktop")
    commands.add_parser("remove-key", help="delete the stored key")
    uninstall_cmd = commands.add_parser("uninstall", help="remove the server from both apps, and delete the key and settings")
    uninstall_cmd.add_argument("--keep-key", action="store_true")
    args = parser.parse_args(argv)

    try:
        match args.command:
            case "setup":
                return setup()
            case "key":
                return set_key(check=not args.no_check)
            case "check":
                return check()
            case "models":
                return list_models(" ".join(args.query) or None)
            case "trigger":
                return set_trigger(args.mode)
            case "folder":
                return set_folder(args.folder)
            case "model":
                return set_model(args.model)
            case "register":
                return register()
            case "remove-key":
                print("Removed the stored key." if key.delete_api_key() else "No key was stored.")
                return 0
            case "uninstall":
                return uninstall(keep_key=args.keep_key)
    except key.MissingKeyError as err:
        print(err)
        return 1
    except openai.AuthenticationError:
        print("OpenAI rejected the stored key. Run `uv run manage.py key` to replace it.")
        return 1
    except openai.APIConnectionError:
        print("Couldn't reach OpenAI. Check your internet connection and try again.")
        return 1
    except KeyboardInterrupt:
        print("\nCancelled.")
        return 130
    return 0


if __name__ == "__main__":
    sys.exit(main())
