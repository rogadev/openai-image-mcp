import base64
import io
from datetime import datetime
from types import SimpleNamespace

import pytest
from PIL import Image as PILImage

from openai_image_mcp import images

NOW = datetime(2026, 11, 15, 14, 32, 5)


def png_bytes(size=(1024, 1024), mode="RGBA") -> bytes:
    out = io.BytesIO()
    PILImage.new(mode, size, (200, 30, 30, 128) if mode == "RGBA" else (200, 30, 30)).save(out, format="PNG")
    return out.getvalue()


def fake_response(count=1, data=None):
    payload = base64.b64encode(data or png_bytes()).decode()
    return SimpleNamespace(data=[SimpleNamespace(b64_json=payload) for _ in range(count)])


class FakeImages:
    def __init__(self):
        self.calls = []

    def generate(self, **kwargs):
        self.calls.append(("generate", kwargs))
        return fake_response(kwargs.get("n", 1))

    def edit(self, **kwargs):
        self.calls.append(("edit", kwargs))
        return fake_response(kwargs.get("n", 1))


class FakeClient:
    def __init__(self):
        self.images = FakeImages()


def call_generate(client, tmp_path, **overrides):
    options = dict(size="auto", quality="low", background="auto", output_format="png", n=1, model="m", folder=tmp_path, now=NOW)
    options.update(overrides)
    return images.generate(client, "A red fox, watercolor!", **options)


@pytest.mark.parametrize("size", ["auto", "1024x1024", "1536x1024", "1024x1536", "3840x2160"])
def test_validate_size_accepts_supported_sizes(size):
    assert images.validate_size(size) == size


@pytest.mark.parametrize(
    ("size", "reason"),
    [
        ("big", "WIDTHxHEIGHT"),
        ("1000x1000", "multiples of 16"),
        ("4096x2048", "3840"),
        ("3072x768", "aspect ratio"),
        ("512x512", "total pixels"),
    ],
)
def test_validate_size_rejects_unsupported_sizes(size, reason):
    with pytest.raises(images.ImageRequestError, match=reason):
        images.validate_size(size)


def test_transparent_background_requires_png_or_webp():
    with pytest.raises(images.ImageRequestError, match="transparent"):
        images.validate_options(1, "transparent", "jpeg")
    images.validate_options(1, "transparent", "webp")


@pytest.mark.parametrize("n", [0, 5])
def test_n_out_of_range_is_rejected(n):
    with pytest.raises(images.ImageRequestError, match="n must be"):
        images.validate_options(n, "auto", "png")


def test_slugify_keeps_first_words_ascii():
    assert images.slugify("A red fox, watercolor! In the snow at dusk") == "a-red-fox-watercolor-in-the"
    assert images.slugify("!!!") == "image"


def test_generate_saves_full_image_and_returns_small_preview(tmp_path):
    client = FakeClient()
    saved = call_generate(client, tmp_path)

    assert len(saved) == 1
    assert saved[0].path == tmp_path / "2026-11-15_143205_a-red-fox-watercolor.png"
    assert saved[0].path.read_bytes() == png_bytes()
    with PILImage.open(io.BytesIO(saved[0].preview_jpeg)) as preview:
        assert preview.format == "JPEG"
        assert max(preview.size) == images.PREVIEW_MAX_EDGE
    _, kwargs = client.images.calls[0]
    assert kwargs == {"prompt": "A red fox, watercolor!", "model": "m", "quality": "low"}


def test_multiple_images_get_numbered_names(tmp_path):
    saved = call_generate(FakeClient(), tmp_path, n=3)
    assert [p.path.name for p in saved] == [f"2026-11-15_143205_a-red-fox-watercolor-{i}.png" for i in (1, 2, 3)]


def test_existing_files_are_never_overwritten(tmp_path):
    first = call_generate(FakeClient(), tmp_path)[0].path
    second = call_generate(FakeClient(), tmp_path)[0].path
    assert first != second
    assert first.exists() and second.exists()


def test_jpeg_output_uses_jpg_extension(tmp_path):
    saved = call_generate(FakeClient(), tmp_path, output_format="jpeg")
    assert saved[0].path.suffix == ".jpg"


def test_output_folder_is_created(tmp_path):
    folder = tmp_path / "nested" / "out"
    call_generate(FakeClient(), folder)
    assert folder.is_dir()


def test_empty_response_is_an_error(tmp_path):
    with pytest.raises(images.ImageRequestError, match="no image data"):
        images.save_results(SimpleNamespace(data=[]), "p", "png", tmp_path, NOW)


def jpeg_bytes() -> bytes:
    out = io.BytesIO()
    PILImage.new("RGB", (64, 64), (10, 20, 30)).save(out, format="JPEG")
    return out.getvalue()


def call_edit(tmp_path, paths, mask_path=None, client=None):
    return images.edit(
        client or FakeClient(), "make it blue", [str(p) for p in paths], mask_path=mask_path and str(mask_path),
        size="auto", quality="low", background="auto", output_format="png", n=1, model="m",
        folder=tmp_path / "out", now=NOW,
    )


def test_edit_uploads_the_checked_bytes_with_the_right_type(tmp_path):
    source, photo, mask = tmp_path / "in.png", tmp_path / "photo.JPG", tmp_path / "mask.png"
    source.write_bytes(png_bytes())
    photo.write_bytes(jpeg_bytes())
    mask.write_bytes(png_bytes())
    client = FakeClient()

    saved = call_edit(tmp_path, [source, photo], mask, client)

    assert saved[0].path.exists()
    _, kwargs = client.images.calls[0]
    assert kwargs["image"] == [("in.png", png_bytes(), "image/png"), ("photo.JPG", jpeg_bytes(), "image/jpeg")]
    assert kwargs["mask"] == ("mask.png", png_bytes(), "image/png")


@pytest.mark.parametrize(
    ("name", "content", "reason"),
    [
        ("id_ed25519", b"-----BEGIN OPENSSH PRIVATE KEY-----", "allowed image type"),
        ("notes.txt", b"hello", "allowed image type"),
        ("secrets.png", b"sk-proj-not-really-an-image", "readable image"),
        ("swapped.png", None, "allowed image type"),
    ],
)
def test_edit_refuses_anything_that_isnt_an_allowed_image(tmp_path, name, content, reason):
    path = tmp_path / name
    path.write_bytes(jpeg_bytes() if content is None else content)
    client = FakeClient()
    with pytest.raises(images.ImageRequestError, match=reason):
        call_edit(tmp_path, [path], client=client)
    assert client.images.calls == [], "nothing may be uploaded"


def test_edit_mask_must_be_png(tmp_path):
    source, mask = tmp_path / "in.png", tmp_path / "mask.jpg"
    source.write_bytes(png_bytes())
    mask.write_bytes(jpeg_bytes())
    with pytest.raises(images.ImageRequestError, match="allowed image type"):
        call_edit(tmp_path, [source], mask)


def test_edit_refuses_oversized_files(tmp_path, monkeypatch):
    source = tmp_path / "in.png"
    source.write_bytes(png_bytes())
    monkeypatch.setattr(images, "MAX_SOURCE_BYTES", 10)
    with pytest.raises(images.ImageRequestError, match="larger than"):
        call_edit(tmp_path, [source])


def test_edit_reports_missing_files(tmp_path):
    with pytest.raises(images.ImageRequestError, match="isn't a file"):
        call_edit(tmp_path, [tmp_path / "nope.png"])
    with pytest.raises(images.ImageRequestError, match="isn't a file"):
        call_edit(tmp_path, [tmp_path])


def test_output_dir_follows_environment(monkeypatch, tmp_path):
    monkeypatch.delenv("IMAGE_OUTPUT_DIR", raising=False)
    assert images.output_dir() == images.DEFAULT_OUTPUT_DIR
    monkeypatch.setenv("IMAGE_OUTPUT_DIR", str(tmp_path))
    assert images.output_dir() == tmp_path


def test_request_options_send_only_what_differs_from_defaults():
    assert images.request_options("gpt-image-2.6-flare", "auto", "auto", "auto", "png", 1) == {
        "model": "gpt-image-2.6-flare"
    }
    assert images.request_options("gpt-image-2.5-flare", "1536x1024", "high", "transparent", "webp", 2) == {
        "model": "gpt-image-2.5-flare",
        "size": "1536x1024",
        "quality": "high",
        "background": "transparent",
        "output_format": "webp",
        "n": 2,
    }


def test_dall_e_models_ask_for_base64():
    assert images.request_options("dall-e-3", "auto", "auto", "auto", "png", 1)["response_format"] == "b64_json"


@pytest.mark.parametrize(
    ("source", "label"),
    [
        ("2026-11-15_143205_a-cat-on-a-sofa.png", "a-cat-on-a-sofa-v2"),
        ("2026-11-15_143300_a-cat-on-a-sofa-v2.png", "a-cat-on-a-sofa-v3"),
        ("2026-11-15_143400_a-cat-on-a-sofa-v9.jpg", "a-cat-on-a-sofa-v10"),
        ("2026-11-15_143205_logo-1024.png", "logo-1024-v2"),
        ("My Living Room.JPG", "my-living-room-edit"),
    ],
)
def test_revision_label_numbers_versions(source, label):
    assert images.revision_label(source) == label


def test_revision_chain_names_each_version_after_the_original(tmp_path):
    first = call_generate(FakeClient(), tmp_path)[0].path
    assert first.name == "2026-11-15_143205_a-red-fox-watercolor.png"

    second = call_edit(tmp_path, [first])[0].path
    third = call_edit(tmp_path, [second])[0].path
    assert second.name == "2026-11-15_143205_a-red-fox-watercolor-v2.png"
    assert third.name == "2026-11-15_143205_a-red-fox-watercolor-v3.png"
    assert first.exists() and second.exists(), "earlier versions are kept for undo"


def test_combining_several_images_is_named_from_the_prompt(tmp_path):
    a, b = tmp_path / "a.png", tmp_path / "b.png"
    a.write_bytes(png_bytes())
    b.write_bytes(png_bytes())
    saved = call_edit(tmp_path, [a, b])
    assert saved[0].path.name == "2026-11-15_143205_make-it-blue.png"


def test_input_fidelity_is_sent_only_when_asked_for(tmp_path):
    source = tmp_path / "in.png"
    source.write_bytes(png_bytes())
    client = FakeClient()
    call_edit(tmp_path, [source], client=client)
    assert "input_fidelity" not in client.images.calls[0][1]

    images.edit(
        client, "x", [str(source)], mask_path=None, size="auto", quality="low", background="auto",
        output_format="png", n=1, model="gpt-image-1.5", folder=tmp_path / "out", now=NOW, input_fidelity="high",
    )
    assert client.images.calls[1][1]["input_fidelity"] == "high"
