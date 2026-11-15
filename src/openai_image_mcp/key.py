"""Stores, loads, checks, and removes the OpenAI API key.

The key lives in the operating system's credential store (Windows Credential
Manager, macOS Keychain, or the Secret Service on Linux), never in a file.
"""

import os
from dataclasses import dataclass

import keyring
import keyring.errors
import openai

KEYRING_SERVICE = "openai-image-mcp"
KEYRING_USERNAME = "api-key"

SETUP_HINT = "run `uv run manage.py key` in the openai-image-mcp folder"

# The only place the key is ever sent. Fixed here rather than read from
# OPENAI_BASE_URL, so a changed environment can't redirect the key elsewhere.
OPENAI_API_URL = "https://api.openai.com/v1"


def make_client(api_key: str, timeout: float = 30) -> openai.OpenAI:
    return openai.OpenAI(api_key=api_key, base_url=OPENAI_API_URL, timeout=timeout, max_retries=1)


class MissingKeyError(RuntimeError):
    """Raised when no API key is stored in the credential store or the environment."""


def load_api_key() -> str:
    """Returns the stored key, falling back to OPENAI_API_KEY.

    Raises:
        MissingKeyError: Neither source has a key. The message says how to store one.
    """
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.KeyringError:
        key = None
    key = key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise MissingKeyError(f"No OpenAI API key is stored. To add one, {SETUP_HINT}.")
    return key


def stored_key() -> str | None:
    """Returns the key in the credential store only, ignoring the environment."""
    try:
        return keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.KeyringError:
        return None


def delete_api_key() -> bool:
    """Removes the stored key. Returns whether there was one to remove."""
    removed = False
    # keyring's Windows backend moves an overwritten key to a second entry
    # (api-key@openai-image-mcp) instead of replacing it, so delete until none is left.
    while True:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except keyring.errors.PasswordDeleteError:
            return removed
        removed = True


def save_api_key(key: str) -> None:
    """Replaces any stored key with this one, leaving no copy of the old key behind."""
    delete_api_key()
    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)


def key_problem(key: str) -> str | None:
    """Catches pastes that can't be a key, before bothering OpenAI.

    OpenAI doesn't document a key format, so this deliberately doesn't require
    the usual "sk-" prefix: whether a key is valid is OpenAI's call, made by
    check_api_key. Today's keys start with "sk-" and run about 160 characters.
    """
    if not key:
        return (
            "Nothing was pasted. In the older Windows PowerShell window, Ctrl+V may not paste into "
            "a hidden prompt; right-click to paste instead."
        )
    if any(c.isspace() for c in key):
        return "The pasted text contains spaces or line breaks, so it can't be a single key."
    return None


def paste_summary(key: str) -> str:
    """Describes what was pasted without revealing it."""
    prefix_note = "starts with sk-" if key.startswith("sk-") else "doesn't start with sk-, as OpenAI keys usually do"
    return f"Received {len(key)} characters; it {prefix_note}."


def describe_key(key: str) -> str:
    """A safe way to refer to a key: its length and last four characters."""
    return f"a {len(key)}-character key ending in ...{key[-4:]}"


@dataclass(frozen=True)
class KeyCheck:
    ok: bool
    message: str


def check_api_key(key: str, model: str) -> KeyCheck:
    """Asks OpenAI whether the key works and can see the image model. Costs nothing."""
    client = make_client(key)
    try:
        client.models.retrieve(model)
    except openai.AuthenticationError:
        return KeyCheck(False, "OpenAI rejected this key. It may be mistyped, revoked, or expired.")
    except openai.NotFoundError:
        return KeyCheck(
            False,
            f"The key works, but it can't use {model}. Complete Organization Verification on the "
            "OpenAI platform, or check that the key's project has access to image models.",
        )
    except openai.PermissionDeniedError:
        return KeyCheck(
            True,
            "OpenAI accepted the key, but it isn't allowed to read model details (a restricted key). "
            "Image requests may still work; if they don't, give the key access to images.",
        )
    except openai.APIConnectionError:
        return KeyCheck(False, "Couldn't reach OpenAI. Check your internet connection and try again.")
    except openai.APIError as err:
        return KeyCheck(False, f"OpenAI returned an unexpected error ({type(err).__name__}).")
    return KeyCheck(True, f"OpenAI accepted the key, and it can use {model}.")
