import keyring
import keyring.errors
import pytest
from keyring.backend import KeyringBackend


class WindowsLikeKeyring(KeyringBackend):
    """In-memory keyring that copies keyring's Windows backend quirk.

    Like the real Credential Manager backend, overwriting a password moves the
    old one to a second "{username}@{service}" entry instead of discarding it.
    """

    priority = 1

    def __init__(self):
        super().__init__()
        self.entries: dict[str, tuple[str, str]] = {}

    def get_password(self, service, username):
        for target in (service, f"{username}@{service}"):
            entry = self.entries.get(target)
            if entry and entry[0] == username:
                return entry[1]
        return None

    def set_password(self, service, username, password):
        if service in self.entries:
            old_user, old_password = self.entries[service]
            self.entries[f"{old_user}@{service}"] = (old_user, old_password)
        self.entries[service] = (username, password)

    def delete_password(self, service, username):
        for target in (service, f"{username}@{service}"):
            entry = self.entries.get(target)
            if entry and entry[0] == username:
                del self.entries[target]
                return
        raise keyring.errors.PasswordDeleteError("not found")


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch, tmp_path):
    """Keeps every test away from the real credential store and settings file."""
    previous = keyring.get_keyring()
    backend = WindowsLikeKeyring()
    keyring.set_keyring(backend)
    monkeypatch.setenv("OPENAI_IMAGE_MCP_SETTINGS", str(tmp_path / "settings" / "settings.json"))
    monkeypatch.delenv("IMAGE_MODEL", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    yield backend
    keyring.set_keyring(previous)
