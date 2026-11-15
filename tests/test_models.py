from types import SimpleNamespace

import pytest

from openai_image_mcp import models


def listing(*ids_and_created):
    return [SimpleNamespace(id=i, created=c) for i, c in ids_and_created]


class FakeClient:
    def __init__(self, entries, fail=False):
        self.calls = 0
        self.fail = fail
        self.entries = entries
        self.models = self

    def list(self):
        self.calls += 1
        if self.fail:
            raise RuntimeError("listing failed")
        return self.entries


TYPICAL = listing(
    ("gpt-6-astra", 50),
    ("gpt-image-2.5-sunburst", 30),
    ("gpt-image-2.5-flare", 31),
    ("gpt-image-2.6-sunburst", 40),
    ("gpt-image-2.6-flare", 41),
    ("gpt-image-2.6-flare-2026-09-01", 42),
    ("gpt-image-2", 20),
    ("dall-e-3", 5),
    ("whisper-2", 60),
)


def test_fetch_keeps_only_image_models_newest_first():
    ids = [m.id for m in models.fetch_image_models(FakeClient(TYPICAL))]
    assert ids[0].startswith("gpt-image-2.6")
    assert "gpt-6-astra" not in ids and "whisper-2" not in ids
    assert ids[-1] == "dall-e-3"


def test_latest_prefers_newest_version_alias_and_everyday_variant():
    assert models.pick_latest(models.fetch_image_models(FakeClient(TYPICAL))) == "gpt-image-2.6-flare"


def test_latest_picks_up_a_new_release_automatically():
    newer = TYPICAL + listing(("gpt-image-3-sunburst", 70))
    assert models.pick_latest(models.fetch_image_models(FakeClient(newer))) == "gpt-image-3-sunburst"


def test_latest_falls_back_to_snapshots_when_no_alias_exists():
    only = models.fetch_image_models(FakeClient(listing(("gpt-image-2-2026-04-01", 1))))
    assert models.pick_latest(only) == "gpt-image-2-2026-04-01"
    assert models.pick_latest([]) is None


def test_search_matches_all_words():
    found = models.fetch_image_models(FakeClient(TYPICAL))
    assert [m.id for m in models.search(found, "2.5 flare")] == ["gpt-image-2.5-flare"]
    assert models.search(found, None) == found


def test_default_is_latest_until_pinned(monkeypatch):
    assert models.configured_default() == ("latest", "the built-in default")
    models.save_default("gpt-image-2.5-flare")
    assert models.configured_default() == ("gpt-image-2.5-flare", "the settings file")
    monkeypatch.setenv("IMAGE_MODEL", "gpt-image-2")
    assert models.configured_default() == ("gpt-image-2", "the IMAGE_MODEL environment variable")


def test_resolver_uses_pin_without_listing():
    models.save_default("gpt-image-2.5-flare")
    client = FakeClient(TYPICAL)
    assert models.ModelResolver().resolve(client) == "gpt-image-2.5-flare"
    assert client.calls == 0


def test_resolver_per_request_model_wins():
    assert models.ModelResolver().resolve(FakeClient(TYPICAL), "dall-e-3") == "dall-e-3"


def test_resolver_caches_the_listing_for_an_hour():
    now = [0.0]
    resolver = models.ModelResolver(clock=lambda: now[0])
    client = FakeClient(TYPICAL)
    resolver.resolve(client)
    resolver.resolve(client)
    assert client.calls == 1
    now[0] = models.CACHE_SECONDS + 1
    resolver.resolve(client)
    assert client.calls == 2


def test_resolver_falls_back_when_listing_fails():
    assert models.ModelResolver().resolve(FakeClient([], fail=True)) == models.FALLBACK_MODEL


@pytest.mark.parametrize("model_id", ["gpt-image-2.6-flare", "chatgpt-image-latest", "dall-e-2"])
def test_is_image_model(model_id):
    assert models.is_image_model(model_id)
