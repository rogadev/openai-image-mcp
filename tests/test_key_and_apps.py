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
    ("candidate", "problem"),
    [(KEY_A, None), ("future-format-key-without-sk", None), ("", "Nothing was pasted"), ("sk-a b", "spaces")],
)
def test_key_problem_only_rejects_impossible_pastes(candidate, problem):
    found = key.key_problem(candidate)
    assert (found is None) if problem is None else (problem in found)


def test_paste_summary_reports_length_and_prefix_only():
    assert key.paste_summary(KEY_A) == f"Received {len(KEY_A)} characters; it starts with sk-."
    summary = key.paste_summary("abc_defg1234567890xy")
    assert "20 characters" in summary and "doesn't start with sk-" in summary and "abc" not in summary


def test_describe_key_hides_the_ending_of_short_values():
    assert key.describe_key("abcde") == "a 5-character value"
    assert key.describe_key("abc_defg12345") == "a 13-character value"


def test_set_key_asks_before_sending_a_non_sk_paste(monkeypatch):
    code, out = run_set_key(
        monkeypatch, "abc_defg1234567890xy", key.KeyCheck(True, "accepted", True), answer="n"
    )
    assert code == 1 and "Nothing was sent" in out
    assert run_set_key.sent == [] and key.stored_key() is None


def test_set_key_does_not_save_when_openai_cant_be_reached(monkeypatch):
    code, out = run_set_key(monkeypatch, KEY_A, key.KeyCheck(False, "Couldn't reach OpenAI.", False))
    assert code == 1 and "--no-check" in out and key.stored_key() is None


def test_describe_key_never_shows_the_whole_key():
    text = key.describe_key(KEY_A)
    assert KEY_A not in text and text.endswith("...aaaa")


def run_set_key(monkeypatch, secret, check_result=None, answer="y"):
    output, sent = [], []

    def fake_check(k, m):
        sent.append(k)
        return check_result

    if check_result is not None:
        monkeypatch.setattr(admin.key, "check_api_key", fake_check)
        monkeypatch.setattr(admin, "_resolve_check_model", lambda k: "gpt-image-test")
    code = admin.set_key(
        output.append, lambda prompt: secret, check=check_result is not None, confirm=lambda prompt: answer
    )
    run_set_key.sent = sent
    return code, "\n".join(output)


def test_set_key_rejects_an_empty_paste(monkeypatch):
    code, out = run_set_key(monkeypatch, "")
    assert code == 1 and "right-click" in out and "Nothing was saved" in out
    assert key.stored_key() is None


def test_set_key_lets_openai_judge_unusual_formats(monkeypatch):
    code, out = run_set_key(monkeypatch, "abc_defg1234567890xy", key.KeyCheck(False, "OpenAI rejected this key.", False))
    assert code == 1 and "20 characters" in out and "only once" in out
    assert "abc_defg" not in out and key.stored_key() is None


def test_set_key_refuses_a_key_openai_rejects(monkeypatch):
    code, out = run_set_key(monkeypatch, KEY_A, key.KeyCheck(False, "OpenAI rejected this key.", False))
    assert code == 1 and key.stored_key() is None


def test_set_key_saves_and_then_replaces(monkeypatch):
    good = key.KeyCheck(True, "OpenAI accepted the key.", True)
    code, out = run_set_key(monkeypatch, KEY_A, good)
    assert code == 0 and "Saved a" in out and key.stored_key() == KEY_A
    code, out = run_set_key(monkeypatch, KEY_B, good)
    assert code == 0 and "Replaced the stored key" in out and key.stored_key() == KEY_B
    assert KEY_A not in out and KEY_B not in out


def test_set_key_keeps_a_key_that_works_but_lacks_model_access(monkeypatch):
    check = key.KeyCheck(False, "The key works, but it can't use gpt-image-test.", True)
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
    assert not admin.settings.settings_path().exists()
    assert "mcpServers" not in json.loads(config.read_text(encoding="utf-8"))
    text = "\n".join(output)
    assert "Claude Code: removed." in text and "Key: removed" in text and KEY_A not in text


def test_uninstall_can_keep_the_key(monkeypatch):
    key.save_api_key(KEY_A)
    monkeypatch.setattr(apps, "desktop_config_paths", lambda: [])
    monkeypatch.setattr(apps, "remove_from_claude_code", lambda: None)
    admin.uninstall(lambda line: None, keep_key=True)
    assert key.stored_key() == KEY_A


def test_client_always_targets_openai_even_if_the_environment_says_otherwise(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", "https://attacker.example/v1")
    client = key.make_client(KEY_A)
    assert str(client.base_url).rstrip("/") == "https://api.openai.com/v1"


def _api_error(cls, status):
    import httpx2 as httpx

    request = httpx.Request("GET", "https://api.openai.com/v1/models/m")
    return cls("error", response=httpx.Response(status, request=request), body=None)


@pytest.mark.parametrize(
    ("raised", "ok", "key_valid"),
    [
        (None, True, True),
        ("auth", False, False),
        ("not_found", False, True),
        ("permission", True, True),
        ("connection", False, False),
        ("server", False, False),
    ],
)
def test_check_api_key_classifies_every_openai_outcome(monkeypatch, raised, ok, key_valid):
    import httpx2 as httpx
    import openai

    errors = {
        "auth": lambda: _api_error(openai.AuthenticationError, 401),
        "not_found": lambda: _api_error(openai.NotFoundError, 404),
        "permission": lambda: _api_error(openai.PermissionDeniedError, 403),
        "connection": lambda: openai.APIConnectionError(request=httpx.Request("GET", "https://api.openai.com")),
        "server": lambda: _api_error(openai.InternalServerError, 500),
    }

    class FakeModels:
        def retrieve(self, model):
            if raised:
                raise errors[raised]()

    monkeypatch.setattr(key, "make_client", lambda api_key: type("C", (), {"models": FakeModels()})())
    result = key.check_api_key(KEY_A, "gpt-image-test")
    assert (result.ok, result.key_valid) == (ok, key_valid)
    assert KEY_A not in result.message


def test_folder_command_sets_shows_and_resets(tmp_path):
    from openai_image_mcp import images

    output = []
    target = tmp_path / "My Art" / "AI"
    assert admin.set_folder(f'"{target}"', output.append) == 0
    assert target.is_dir() and list(target.iterdir()) == [], "the write probe is cleaned up"
    assert images.output_dir() == target

    admin.set_folder(None, output.append)
    assert f"Images are saved to {target} (from your saved setting)." in output

    assert admin.set_folder("default", output.append) == 0
    assert images.output_dir() == images.default_output_dir()


def test_folder_shortcuts_use_the_real_known_folders(monkeypatch, tmp_path):
    from openai_image_mcp import images

    monkeypatch.setattr(admin.settings, "known_folder", lambda name: tmp_path / f"known-{name}")
    admin.set_folder("Desktop", lambda line: None)
    assert images.output_dir() == tmp_path / "known-desktop"


def test_folder_that_cant_be_written_changes_nothing(tmp_path):
    from openai_image_mcp import images

    blocker = tmp_path / "a-file"
    blocker.write_text("not a folder")
    output = []
    assert admin.set_folder(str(blocker / "sub"), output.append) == 1
    assert "Nothing was changed" in output[-1]
    assert images.output_dir() == images.default_output_dir()


def test_setup_asks_for_the_folder(monkeypatch, tmp_path):
    from openai_image_mcp import images

    key.save_api_key(KEY_A)
    monkeypatch.setattr(admin, "register", lambda out: 0)
    assert admin.setup(lambda line: None, ask=lambda prompt: str(tmp_path / "chosen")) == 0
    assert images.output_dir() == tmp_path / "chosen"

    assert admin.setup(lambda line: None, ask=lambda prompt: "") == 0
    assert images.output_dir() == tmp_path / "chosen", "Enter keeps the current folder"


def test_trigger_command_shows_sets_and_validates():
    from openai_image_mcp import settings

    output = []
    assert settings.trigger_mode() == "implicit"
    assert admin.set_trigger("explicit", output.append) == 0
    assert settings.trigger_mode() == "explicit"
    assert "Restart Claude Desktop" in output[-1]
    assert admin.set_trigger("sometimes", output.append) == 1
    assert settings.trigger_mode() == "explicit"
    admin.set_trigger(None, output.append)
    assert output[-1].startswith("Trigger mode: explicit.")


def test_check_warns_about_a_damaged_settings_file(monkeypatch):
    from openai_image_mcp import settings

    path = settings.settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(apps, "claude_code_registered", lambda: None)
    monkeypatch.setattr(apps, "desktop_config_paths", lambda: [])
    output = []
    assert admin.check(output.append) == 1
    assert any(line.startswith("Warning:") and "can't be read" in line for line in output)
    assert settings.trigger_mode() == "implicit"
