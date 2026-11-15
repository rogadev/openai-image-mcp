"""Works out the next release version and its notes from git history.

Used by .github/workflows/release.yml on every push to main.

Rules, applied to the commits since the last vX.Y.Z tag:
- The pull request into main carries the `release:major` label: bump major.
  Nothing else ever bumps major, including a `!` or BREAKING CHANGE in a commit.
- Any `feat:` commit: bump minor.
- Anything else: bump patch.
- No vX.Y.Z tag yet: release the version in pyproject.toml as is (or its next
  major, with the label).
- No new commits since the last tag (for example, a dev-to-main merge of work
  that was already released from dev): release nothing.

Usage:
    python release_version.py next [--major]   # prints X.Y.Z, or nothing if there's nothing new to release
    python release_version.py notes TAG        # prints Markdown release notes
"""

import argparse
import re
import subprocess
import sys
import tomllib
from pathlib import Path

TAG_PATTERN = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
FEAT = re.compile(r"^feat(\([^)]*\))?!?:")
FIX = re.compile(r"^fix(\([^)]*\))?!?:")
PREFIX = re.compile(r"^[a-z]+(\([^)]*\))?!?:\s*")


def parse_version(text: str) -> tuple[int, int, int]:
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", text.strip())
    if not match:
        raise ValueError(f"Not an X.Y.Z version: {text!r}")
    return int(match[1]), int(match[2]), int(match[3])


def next_version(previous_tag: str | None, subjects: list[str], major: bool, base_version: str) -> str:
    """Returns the version to release, without the leading v."""
    if previous_tag is None:
        x, y, z = parse_version(base_version)
        return f"{x + 1}.0.0" if major else f"{x}.{y}.{z}"
    x, y, z = parse_version(previous_tag)
    if major:
        return f"{x + 1}.0.0"
    if any(FEAT.match(s) for s in subjects):
        return f"{x}.{y + 1}.0"
    return f"{x}.{y}.{z + 1}"


def release_notes(subjects: list[str], previous_tag: str | None, tag: str, repo: str) -> str:
    """Groups commit subjects into Features, Fixes, and Other changes."""
    groups: dict[str, list[str]] = {"Features": [], "Fixes": [], "Other changes": []}
    for subject in subjects:
        heading = "Features" if FEAT.match(subject) else "Fixes" if FIX.match(subject) else "Other changes"
        groups[heading].append(PREFIX.sub("", subject, count=1) if heading != "Other changes" else subject)
    parts = []
    for heading, items in groups.items():
        if items:
            parts.append(f"## {heading}\n\n" + "\n".join(f"- {item}" for item in items))
    if not parts:
        parts.append("No changes since the last release.")
    if previous_tag:
        parts.append(f"**Full changelog:** https://github.com/{repo}/compare/{previous_tag}...{tag}")
    return "\n\n".join(parts) + "\n"


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True, text=True).stdout.strip()


def latest_tag(ref: str = "HEAD") -> str | None:
    tags = [t for t in _git("tag", "--merged", ref, "--list", "v*").splitlines() if TAG_PATTERN.match(t)]
    return max(tags, key=parse_version) if tags else None


def head_is_released() -> bool:
    return any(TAG_PATTERN.match(t) for t in _git("tag", "--points-at", "HEAD").splitlines())


def subjects_since(tag: str | None, end: str = "HEAD") -> list[str]:
    revision = f"{tag}..{end}" if tag else end
    out = _git("log", "--reverse", "--no-merges", "--format=%s", revision)
    return [line for line in out.splitlines() if line]


def pyproject_version() -> str:
    data = tomllib.loads(Path("pyproject.toml").read_text(encoding="utf-8"))
    return data["project"]["version"]


def main() -> int:
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    nxt = commands.add_parser("next")
    nxt.add_argument("--major", action="store_true")
    notes = commands.add_parser("notes")
    notes.add_argument("tag")
    notes.add_argument("--repo", default="")
    args = parser.parse_args()

    if args.command == "next":
        if head_is_released():
            return 0
        previous = latest_tag()
        subjects = subjects_since(previous)
        if previous and not subjects:
            return 0
        print(next_version(previous, subjects, args.major, pyproject_version()))
        return 0

    previous = latest_tag(f"{args.tag}^") if _tag_exists(args.tag) else latest_tag()
    print(release_notes(subjects_since(previous), previous, args.tag, args.repo), end="")
    return 0


def _tag_exists(tag: str) -> bool:
    return bool(_git("tag", "--list", tag))


if __name__ == "__main__":
    sys.exit(main())
