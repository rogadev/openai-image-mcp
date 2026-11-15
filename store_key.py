"""Save the OpenAI API key to Windows Credential Manager for the image server.

Usage:
    uv run store_key.py                  # paste the key at a hidden prompt
    uv run store_key.py --from-file PATH # read the key from a text file
    uv run store_key.py --delete         # remove the stored key

The key is never printed. Only its length and last four characters are shown
so you can confirm the right key was saved.
"""

import argparse
import getpass
import sys
from pathlib import Path

import keyring
import keyring.errors

from openai_image_mcp.key import KEYRING_SERVICE, KEYRING_USERNAME


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    group = parser.add_mutually_exclusive_group()
    group.add_argument("--from-file", type=Path, help="read the key from this text file")
    group.add_argument("--delete", action="store_true", help="remove the stored key")
    args = parser.parse_args()

    if args.delete:
        try:
            keyring.delete_password(KEYRING_SERVICE, KEYRING_USERNAME)
        except keyring.errors.PasswordDeleteError:
            print("No stored key to remove.")
            return 0
        print("Removed the stored key.")
        return 0

    if args.from_file:
        key = args.from_file.read_text(encoding="utf-8-sig").strip()
    else:
        key = getpass.getpass("OpenAI API key (input hidden): ").strip()

    if not key.startswith("sk-"):
        print("That doesn't look like an OpenAI API key (expected it to start with 'sk-'). Nothing saved.")
        return 1

    keyring.set_password(KEYRING_SERVICE, KEYRING_USERNAME, key)
    print(f"Saved a {len(key)}-character key ending in ...{key[-4:]} to Windows Credential Manager.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
