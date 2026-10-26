import io

import pytest
from PIL import Image

from ark_gacha.models import DrawMode, DrawResult
from ark_gacha.render import Renderer


def renderer_fixture(tmp_path, pool):
    try:
        renderer = Renderer(tmp_path)
    except Exception:
        pytest.skip("This rendering test needs a CJK font")
    names = {}
    for chars in pool.roster.values():
        for char in chars:
            names[char] = "很长的干员名字用于排版测试"
            Image.new("RGBA", (128, 128), (255, 100, 50, 180)).save(tmp_path / f"{char}.png")
    return renderer, names


async def test_png_layout_and_well_pagination_preserve_all_six(tmp_path, pool):
    renderer, names = renderer_fixture(tmp_path, pool)
    ten = [DrawResult(index=i + 1, char_id="char_up", rarity=6) for i in range(10)]
    images = await renderer.render(pool, names, ten, DrawMode.TEN, 2)
    with Image.open(io.BytesIO(images[0])) as image:
        assert image.size == (898, 554)
    well = [DrawResult(index=i + 1, char_id="char_up", rarity=6) for i in range(61)]
    well += [DrawResult(index=62, char_id="char_four", rarity=4)]
    images = await renderer.render(pool, names, well, DrawMode.WELL, 0)
    assert len(images) == 3
    for image_bytes, expected_height in zip(images, [1187, 1187, 343], strict=True):
        with Image.open(io.BytesIO(image_bytes)) as image:
            assert image.size == (1068, expected_height)
            image.verify()
    assert len(list(tmp_path.iterdir())) == len(names)  # no result files written


async def test_missing_and_corrupt_avatar_rejected_before_render(tmp_path, pool, monkeypatch):
    renderer, _ = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    path.write_bytes(b"corrupt")
    assert not renderer.valid_image(path)
    path.unlink()
    assert not renderer.valid_image(path)
    with pytest.raises(Exception, match="ID无效"):
        renderer.avatar_path("../../.env")


def test_long_names_fit_card(tmp_path, pool):
    renderer, _ = renderer_fixture(tmp_path, pool)
    from PIL import ImageDraw

    draw = ImageDraw.Draw(Image.new("RGB", (200, 100)))
    name = renderer._fit(draw, "非常非常非常长的干员名称", renderer._font(21), 146)
    assert name.endswith("…")
    assert draw.textlength(name, font=renderer._font(21)) <= 146
