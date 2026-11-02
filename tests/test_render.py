import asyncio
import io

import httpx
import pytest
from PIL import Image, ImageChops

from ark_gacha.models import DrawMode, DrawResult, UserError
from ark_gacha.render import AVATAR_CDN_ROOT, AVATAR_ROOT, FALLBACK_AVATAR, Renderer


def renderer_fixture(tmp_path, pool):
    try:
        renderer = Renderer(tmp_path)
    except UserError:
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
    for image_bytes, expected_height in zip(images, [1317, 1317, 369], strict=True):
        with Image.open(io.BytesIO(image_bytes)) as image:
            assert image.size == (1068, expected_height)
            image.verify()
    assert len(list(tmp_path.iterdir())) == len(names)  # no result files written


async def test_well_labels_use_original_pull_indices_across_pages(tmp_path, pool, monkeypatch):
    from PIL import ImageDraw

    renderer, names = renderer_fixture(tmp_path, pool)
    labels = []
    original_text = ImageDraw.ImageDraw.text

    def capture_text(draw, xy, text, *args, **kwargs):
        if text.startswith("第") and text.endswith("抽"):
            labels.append(text)
        return original_text(draw, xy, text, *args, **kwargs)

    monkeypatch.setattr(ImageDraw.ImageDraw, "text", capture_text)
    six_indices = [1, *range(10, 291, 10), 300]
    results = [
        DrawResult(
            index=i,
            char_id="char_up" if i in six_indices else "char_four",
            rarity=6 if i in six_indices else 4,
        )
        for i in range(1, 301)
    ]
    images = await renderer.render(pool, names, results, DrawMode.WELL, 0)
    assert len(images) == 2
    assert labels == [f"第{i}抽" for i in six_indices]

    labels.clear()
    await renderer.render(pool, names, results[:10], DrawMode.TEN, 2)
    assert not labels


async def test_missing_and_corrupt_avatar_detected_and_invalid_id_rejected(tmp_path, pool):
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


def paused_downloads(renderer, body):
    started, release = asyncio.Event(), asyncio.Event()
    requests = []

    async def handle(request):
        requests.append(request.url.path)
        started.set()
        await release.wait()
        return httpx.Response(200, content=body)

    renderer.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    return started, release, requests


def assert_first_avatar_is_fallback(content, renderer):
    expected = Image.new("RGB", (128, 128), "#223043")
    expected.paste(renderer.fallback, (0, 0), renderer.fallback)
    with Image.open(io.BytesIO(content)) as image:
        actual = image.crop((39, 82, 167, 210))
        assert ImageChops.difference(actual, expected).getbbox() is None


def test_bundled_fallback_is_the_requested_transparent_180_png():
    with Image.open(FALLBACK_AVATAR) as image:
        assert image.format == "PNG" and image.size == (180, 180) and image.mode == "RGBA"
        assert image.getchannel("A").getextrema() == (0, 255)


def test_unsafe_avatar_dimensions_use_fallback(tmp_path, pool, monkeypatch):
    renderer, _ = renderer_fixture(tmp_path, pool)
    monkeypatch.setattr(Image, "MAX_IMAGE_PIXELS", 1)
    assert not renderer.valid_image(tmp_path / "char_up.png")
    assert renderer._avatar("char_up") is renderer.fallback


@pytest.mark.parametrize("mode", [DrawMode.TEN, DrawMode.WELL])
@pytest.mark.parametrize("corrupt", [False, True])
async def test_render_uses_fallback_while_download_is_pending_and_real_avatar_afterwards(
    tmp_path, pool, mode, corrupt
):
    renderer, names = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    body = path.read_bytes()
    if corrupt:
        path.write_bytes(b"broken avatar")
    else:
        path.unlink()
    started, release, requests = paused_downloads(renderer, body)
    results = [DrawResult(index=78, char_id="char_up", rarity=6)]
    try:
        images = await asyncio.wait_for(renderer.render(pool, names, results, mode, 0), 2)
        await asyncio.wait_for(started.wait(), 2)
        assert_first_avatar_is_fallback(images[0], renderer)
        assert not renderer.valid_image(path)
        assert len(requests) == 1
        release.set()
        await renderer.ensure(["char_up"])
        assert renderer.valid_image(path)
        real = await renderer.render(pool, names, results, mode, 0)
        assert real != images and len(requests) == 1
    finally:
        await renderer.close()


async def test_concurrent_draws_share_one_avatar_download(tmp_path, pool):
    renderer, names = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    body = path.read_bytes()
    path.unlink()
    started, release, requests = paused_downloads(renderer, body)
    results = [DrawResult(index=i + 1, char_id="char_up", rarity=6) for i in range(10)]
    try:
        replies = await asyncio.wait_for(
            asyncio.gather(
                *(renderer.render(pool, names, results, DrawMode.TEN, 2) for _ in range(4))
            ),
            3,
        )
        await asyncio.wait_for(started.wait(), 2)
        assert all(reply == replies[0] for reply in replies)
        assert len(requests) == 1 and len(renderer.downloads) == 1
        release.set()
        await renderer.ensure(["char_up"])
        assert len(requests) == 1
    finally:
        await renderer.close()


async def test_render_does_not_wait_for_pool_preload(tmp_path, pool):
    renderer, names = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    body = path.read_bytes()
    path.unlink()
    started, release, requests = paused_downloads(renderer, body)
    preload = asyncio.create_task(renderer.prepare(pool))
    try:
        await asyncio.wait_for(started.wait(), 2)
        images = await asyncio.wait_for(
            renderer.render(
                pool, names, [DrawResult(index=1, char_id="char_up", rarity=6)], DrawMode.TEN, 2
            ),
            2,
        )
        assert not preload.done()
        assert_first_avatar_is_fallback(images[0], renderer)
        assert len(requests) == 1
        release.set()
        await preload
    finally:
        await renderer.close()
        await asyncio.gather(preload, return_exceptions=True)


async def test_failed_download_keeps_rendering_but_strict_validation_reports_failure(
    tmp_path, pool
):
    renderer, names = renderer_fixture(tmp_path, pool)
    (tmp_path / "char_up.png").unlink()
    renderer.client = httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(404))
    )
    results = [DrawResult(index=1, char_id="char_up", rarity=6)]
    try:
        images = await renderer.render(pool, names, results, DrawMode.TEN, 2)
        assert_first_avatar_is_fallback(images[0], renderer)
        with pytest.raises(UserError, match="头像资源未准备好"):
            await renderer.ensure(["char_up"])
        repeated = await renderer.render(pool, names, results, DrawMode.TEN, 2)
        assert repeated == images
    finally:
        await renderer.close()


@pytest.mark.parametrize("failure", ["timeout", "http", "corrupt", "oversized"])
async def test_avatar_switches_from_cdn_to_raw_and_reuses_verified_cache(tmp_path, pool, failure):
    renderer, _ = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    body = path.read_bytes()
    path.unlink()
    requests = []

    def handle(request):
        url = str(request.url)
        requests.append(url)
        if url.startswith(AVATAR_CDN_ROOT):
            if failure == "timeout":
                raise httpx.ConnectTimeout("fixture", request=request)
            if failure == "http":
                return httpx.Response(503)
            if failure == "oversized":
                return httpx.Response(200, content=b"x" * (4 * 1024 * 1024 + 1))
            return httpx.Response(200, content=b"not a PNG")
        return httpx.Response(200, content=body)

    renderer.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        await renderer.ensure(["char_up"])
        assert requests == [f"{AVATAR_CDN_ROOT}/char_up.png", f"{AVATAR_ROOT}/char_up.png"]
        assert renderer.valid_image(path) and path.read_bytes() == body
        await renderer.ensure(["char_up"])
        assert len(requests) == 2
        assert not list(tmp_path.glob("*.tmp"))
    finally:
        await renderer.close()


async def test_avatar_cdn_success_does_not_request_raw(tmp_path, pool):
    renderer, _ = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    body = path.read_bytes()
    path.write_bytes(b"corrupt cached image")
    requests = []

    def handle(request):
        requests.append(str(request.url))
        return httpx.Response(200, content=body)

    renderer.client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    try:
        await renderer.ensure(["char_up"])
        assert requests == [f"{AVATAR_CDN_ROOT}/char_up.png"]
        assert path.read_bytes() == body
    finally:
        await renderer.close()


async def test_close_cancels_pending_downloads_and_closes_http_client(tmp_path, pool):
    renderer, names = renderer_fixture(tmp_path, pool)
    path = tmp_path / "char_up.png"
    body = path.read_bytes()
    path.unlink()
    started, _, _ = paused_downloads(renderer, body)
    await renderer.render(
        pool, names, [DrawResult(index=1, char_id="char_up", rarity=6)], DrawMode.TEN, 2
    )
    await asyncio.wait_for(started.wait(), 2)
    task = renderer.downloads["char_up"]
    await renderer.close()
    assert task.cancelled() and not renderer.downloads and renderer.client.is_closed


async def test_fallback_draws_commit_normally_and_history_keeps_original_image(app):
    service, clock = app
    renderer = Renderer(service.settings.data_dir / "avatars")
    started, _, _ = paused_downloads(renderer, b"unused until released")
    service.renderer = renderer
    try:
        ten = await asyncio.wait_for(service.handle("u", "g1", "ten", "方舟十连"), 3)
        await asyncio.wait_for(started.wait(), 2)
        assert ten.images and not ten.texts
        assert_first_avatar_is_fallback(ten.images[0], renderer)
        before = await service.store.read("u", "NORMAL_TEST", "standard", clock())
        assert before.ten == 1 and before.state.pulls == 10
        well = await asyncio.wait_for(service.handle("u", "g1", "well", "方舟一井"), 3)
        assert well.images and not well.texts
        after = await service.store.read("u", "NORMAL_TEST", "standard", clock())
        assert after.well == 1 and after.state == before.state
        history = await service.handle("u", "g1", "history", "查看历史")
        assert history.images == ten.images
    finally:
        await renderer.close()
