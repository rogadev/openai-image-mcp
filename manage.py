"""Set up and manage openai-image-mcp. Run `uv run manage.py --help` for the commands."""

import sys

from openai_image_mcp.admin import main

if __name__ == "__main__":
    sys.exit(main())
