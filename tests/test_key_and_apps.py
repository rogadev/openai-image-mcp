import json
from pathlib import Path

import pytest

from openai_image_mcp import admin, apps, key

KEY_A = "sk-proj-" + "a" * 60
KEY_B = "sk-proj-" + "b" * 60


def test_replacing_the_key_leaves_no_old_copy(fake_keyring):
    key.save_api_key(KEY_A)
    key.save_api_key(KEY_B)
    assert key.load_api_key() == KEY_B
    assert [v for _, v in fake_keyring.entries.values()] == [KEY_B]


def test_delete_removes_every_copy(fake_keyring):
    # Simulate a key replaced by an older version of this tool, which left a second entry.
    fake_keyring.set_password(key.KEYRING_SERVICE, key.KEYRING_USERNAME, KEY_A)
    fake_keyring.set_password(key.KEYRING_SERVICE, key.KEYRING_USERNAME, KEY_B)
    assert len(fake_keyring.entries) == 2
    assert key.delete_api_key() is True
    assert fake_keyring.entries == {}
    assert key.delete_api_key() is False


@pytest.mark.parametrize(
    ("candidate", "ok"),
    [(KEY_A, True), ("abc_defg1234567890xy", False), ("sk-short", False), ("sk-" + "a" * 40 + " x", False)],
)
def test_looks_like_openai_key(candidate, ok):
    assert key.looks_like_openai_key(candidate) is ok


def test_describe_key_never_shows_the_whole_key():
    text = key.describe_key(KEY_A)
    assert KEY_A not in text and text.endswith("...aaaa")


def run_set_key(monkeypatch, secret, check_result=None):
    output = []
    if check_result is not None:
        monkeypatch.setattr(admin.key, "check_api_key", lambda k, m: check_result)
        monkeypatch.setattr(admin, "_resolve_check_model", lambda k: "gpt-image-test")
    code = admin.set_key(output.append, lambda prompt: secret, check=check_result is not None)
    return code, "\n".join(output)


def test_set_key_rejects_text_that_isnt_a_key(monkeypatch):
    code, out = run_set_key(monkeypatch, "abc_defg1234567890xy")
    assert code == 1 and "Nothing was saved" in out
    assert key.stored_key() is None


def test_set_key_refuses_a_key_openai_rejects(monkeypatch):
    code, out = run_set_key(monkeypatch, KEY_A, key.KeyCheck(False, "OpenAI rejected this key."))
    assert code == 1 and key.stored_key() is None


def test_set_key_saves_and_then_replaces(monkeypatch):
    good = key.KeyCheck(True, "OpenAI accepted the key.")
    code, out = run_set_key(monkeypatch, KEY_A, good)
    assert code == 0 and "Saved a" in out and key.stored_key() == KEY_A
    code, out = run_set_key(monkeypatch, KEY_B, good)
    assert code == 0 and "Replaced the stored key" in out and key.stored_key() == KEY_B
    assert KEY_A not in out and KEY_B not in out


def test_set_key_keeps_a_key_that_works_but_lacks_model_access(monkeypatch):
    check = key.KeyCheck(False, "The key works, but it can't use gpt-image-test.")
    code, _ = run_set_key(monkeypatch, KEY_A, check)
    assert code == 0 and key.stored_key() == KEY_A


def write_config(path: Path, data: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_desktop_config_discovery_covers_store_and_standard_installs(tmp_path):
    appdata, local = tmp_path / "Roaming", tmp_path / "Local"
    standard = write_config(appdata / "Claude" / apps.CONFIG_NAME, {})
    store = write_config(local / "Claude-Data" / "claude_abc123" / apps.CONFIG_NAME, {})
    write_config(local / "Packages" / "Claude_abc" / "LocalCache" / "Roaming" / "Claude-3p" / apps.CONFIG_NAME, {})
    found = apps.desktop_config_paths({"APPDATA": str(appdata), "LOCALAPPDATA": str(local)}, tmp_path / "home")
    assert found == [standard, store]


def test_desktop_register_and_unregister_keep_other_settings(tmp_path):
    path = write_config(
        tmp_path / apps.CONFIG_NAME,
        {"preferences": {"theme": "dark"}, "mcpServers": {"other": {"command": "x"}}},
    )
    exe = Path("C:/tools/openai-image-mcp.exe")

    assert apps.add_to_desktop_config(path, exe) is True
    assert apps.add_to_desktop_config(path, exe) is False
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"][apps.SERVER_NAME] == {"command": str(exe), "args": []}
    assert data["preferences"] == {"theme": "dark"}
    assert (tmp_path / (apps.CONFIG_NAME + apps.BACKUP_SUFFIX)).exists()

    assert apps.remove_from_desktop_config(path) is True
    assert apps.remove_from_desktop_config(path) is False
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["mcpServers"] == {"other": {"command": "x"}}
    assert data["preferences"] == {"theme": "dark"}


def test_reregistering_keeps_custom_env(tmp_path):
    path = write_config(
        tmp_path / apps.CONFIG_NAME,
        {"mcpServers": {apps.SERVER_NAME: {"command": "old.exe", "args": [], "env": {"IMAGE_MODEL": "x"}}}},
    )
    apps.add_to_desktop_config(path, Path("new.exe"))
    entry = json.loads(path.read_text(encoding="utf-8"))["mcpServers"][apps.SERVER_NAME]
    assert entry == {"command": "new.exe", "args": [], "env": {"IMAGE_MODEL": "x"}}


def test_uninstall_removes_everything_it_installed(monkeypatch, tmp_path, fake_keyring):
    key.save_api_key(KEY_A)
    admin.models.save_default("gpt-image-2.5-flare")
    config = write_config(tmp_path / apps.CONFIG_NAME, {"mcpServers": {apps.SERVER_NAME: {"command": "x"}}})
    monkeypatch.setattr(apps, "desktop_config_paths", lambda: [config])
    monkeypatch.setattr(apps, "remove_from_claude_code", lambda: apps.CommandResult(True, ""))

    output = []
    assert admin.uninstall(output.append) == 0

    assert fake_keyring.entries == {}
    assert not admin.models.settings_path().exists()
    assert "mcpServers" not in json.loads(config.read_text(encoding="utf-8"))
    text = "\n".join(output)
    assert "Claude Code: removed." in text and "Key: removed" in text and KEY_A not in text


def test_uninstall_can_keep_the_key(monkeypatch):
    key.save_api_key(KEY_A)
    monkeypatch.setattr(apps, "desktop_config_paths", lambda: [])
    monkeypatch.setattr(apps, "remove_from_claude_code", lambda: None)
    admin.uninstall(lambda line: None, keep_key=True)
    assert key.stored_key() == KEY_A
