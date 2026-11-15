import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / ".github" / "scripts" / "release_version.py"
spec = importlib.util.spec_from_file_location("release_version", SCRIPT)
rv = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rv)


@pytest.mark.parametrize(
    ("previous", "subjects", "major", "expected"),
    [
        ("v1.2.3", ["fix: crash on empty prompt"], False, "1.2.4"),
        ("v1.2.3", ["docs: typo", "chore: bump deps"], False, "1.2.4"),
        ("v1.2.3", [], False, "1.2.4"),
        ("v1.2.3", ["fix: a", "feat: add edit tool"], False, "1.3.0"),
        ("v1.2.3", ["feat(server): scoped feature"], False, "1.3.0"),
        ("v1.2.3", ["fix: a"], True, "2.0.0"),
        ("v1.9.9", ["feat!: breaking change"], False, "1.10.0"),
        ("v1.2.3", ["fix: x\n\nBREAKING CHANGE: y"], False, "1.2.4"),
        ("v0.1.0", ["featuring: not a prefix"], False, "0.1.1"),
    ],
)
def test_next_version(previous, subjects, major, expected):
    assert rv.next_version(previous, subjects, major, "0.1.0") == expected


def test_first_release_uses_pyproject_version():
    assert rv.next_version(None, ["feat: x"], False, "0.1.0") == "0.1.0"
    assert rv.next_version(None, ["fix: x"], True, "0.1.0") == "1.0.0"


def test_release_notes_group_and_strip_prefixes():
    notes = rv.release_notes(
        ["feat(server): add edit tool", "fix: handle empty data", "docs: update README"],
        "v0.1.0",
        "v0.2.0",
        "rogadev/openai-image-mcp",
    )
    assert "## Features\n\n- add edit tool" in notes
    assert "## Fixes\n\n- handle empty data" in notes
    assert "## Other changes\n\n- docs: update README" in notes
    assert notes.rstrip().endswith("compare/v0.1.0...v0.2.0")


def git(repo: Path, *args: str) -> str:
    return subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "-q", "-b", "main")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "x"\nversion = "0.1.0"\n')
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-q", "-m", "feat: initial")
    return tmp_path


def run_script(repo: Path, *args: str) -> str:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def test_cli_against_real_git_history(repo):
    assert run_script(repo, "next") == "0.1.0"
    git(repo, "tag", "v0.1.0")
    assert run_script(repo, "next") == "", "an already-tagged HEAD releases nothing"

    git(repo, "commit", "-q", "--allow-empty", "-m", "fix: small thing")
    assert run_script(repo, "next") == "0.1.1"
    git(repo, "commit", "-q", "--allow-empty", "-m", "feat: new thing")
    assert run_script(repo, "next") == "0.2.0"
    assert run_script(repo, "next", "--major") == "1.0.0"

    notes = run_script(repo, "notes", "v0.2.0", "--repo", "o/r")
    assert "- new thing" in notes and "- small thing" in notes and "initial" not in notes


def test_merge_commits_are_ignored(repo):
    git(repo, "tag", "v0.1.0")
    git(repo, "switch", "-q", "-c", "dev")
    git(repo, "commit", "-q", "--allow-empty", "-m", "fix: on dev")
    git(repo, "switch", "-q", "main")
    git(repo, "merge", "-q", "--no-ff", "dev", "-m", "Merge pull request #1 from o/dev")
    assert run_script(repo, "next") == "0.1.1"
    assert "Merge pull request" not in run_script(repo, "notes", "v0.1.1")
