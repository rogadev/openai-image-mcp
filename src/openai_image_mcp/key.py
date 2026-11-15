"""Loads the OpenAI API key without it ever living in a config file."""

import os

import keyring
import keyring.errors

KEYRING_SERVICE = "openai-image-mcp"
KEYRING_USERNAME = "api-key"


class MissingKeyError(RuntimeError):
    """Raised when no API key is stored in Credential Manager or the environment."""


def load_api_key() -> str:
    """Returns the key from Windows Credential Manager, falling back to OPENAI_API_KEY.

    Raises:
        MissingKeyError: Neither source has a key. The message says how to store one.
    """
    try:
        key = keyring.get_password(KEYRING_SERVICE, KEYRING_USERNAME)
    except keyring.errors.KeyringError:
        key = None
    key = key or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise MissingKeyError(
            "No OpenAI API key found. Store one in Windows Credential Manager by running "
            "`uv run store_key.py` in the openai-image-mcp folder, then restart the app."
        )
    return key
