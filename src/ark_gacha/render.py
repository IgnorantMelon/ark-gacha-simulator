import asyncio
import io
import logging
import os
import re
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .models import DrawMode, DrawResult, PoolDefinition, UserError

AVATAR_ROOT = "https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/main/avatar"
AVATAR_CDN_ROOT = "https://jsd.onmicrosoft.cn/gh/yuanyan3060/ArknightsGameResource@main/avatar"
AVATAR_TIMEOUT = httpx.Timeout(10, connect=4)
COLORS = {3: "#83a8c0", 4: "#aa93de", 5: "#f3c45d", 6: "#ff9c43"}
FALLBACK_AVATAR = Path(__file__).parent / "assets" / "unknown-operator-180.png"
log = logging.getLogger(__name__)


class Renderer:
    def __init__(self, root: Path, font_path: str = ""):
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)
        candidates = (
            [font_path]
            if font_path
            else [
                "C:/Windows/Fonts/msyh.ttc",
                "C:/Windows/Fonts/simhei.ttf",
                "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
            ]
        )
        self.font_path = next((p for p in candidates if p and Path(p).is_file()), "")
        if not self.font_path:
            raise UserError("未找到中文字体，请配置 ARK_FONT_PATH。")
        self.download_limit = asyncio.Semaphore(6)
        self.downloads: dict[str, asyncio.Task[None]] = {}
        self.client: httpx.AsyncClient | None = None
        self.closed = False
        with Image.open(FALLBACK_AVATAR) as image:
            self.fallback = image.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS)

    def avatar_path(self, char_id: str) -> Path:
        if not re.fullmatch(r"char_[A-Za-z0-9_]+", char_id):
            raise UserError("头像干员ID无效。")
        return self.root / f"{char_id}.png"

    @staticmethod
    def valid_image(path: Path) -> bool:
        try:
            with Image.open(path) as image:
                if image.format != "PNG" or max(image.size) > 2048:
                    return False
                image.verify()
            with Image.open(path) as image:
                image.load()
            return True
        except (
            OSError,
            ValueError,
            SyntaxError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            return False

    async def ensure(self, characters: list[str]) -> None:
        results = await asyncio.gather(*self.queue_downloads(characters), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    def queue_downloads(self, characters: list[str]) -> list[asyncio.Task[None]]:
        if self.closed:
            raise RuntimeError("Renderer is closed")
        paths = {char_id: self.avatar_path(char_id) for char_id in set(characters)}
        tasks = []
        for char_id, path in paths.items():
            task = self.downloads.get(char_id)
            if task is None:
                task = asyncio.create_task(self._download(char_id, path))
                self.downloads[char_id] = task
                task.add_done_callback(lambda done, char=char_id: self._download_done(char, done))
            tasks.append(task)
        return tasks

    def _download_done(self, char_id: str, task: asyncio.Task[None]) -> None:
        self.downloads.pop(char_id, None)
        if not task.cancelled() and (exc := task.exception()) is not None:
            log.warning("Avatar download failed (%s)", type(exc).__name__)

    async def _download(self, char_id: str, path: Path) -> None:
        if await asyncio.to_thread(self.valid_image, path):
            return
        async with self.download_limit:
            if self.client is None:
                self.client = httpx.AsyncClient(follow_redirects=True)
            for attempt, root in enumerate((AVATAR_CDN_ROOT, AVATAR_ROOT, AVATAR_CDN_ROOT)):
                try:
                    async with self.client.stream(
                        "GET", f"{root}/{char_id}.png", timeout=AVATAR_TIMEOUT
                    ) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            body.extend(chunk)
                            if len(body) > 4 * 1024 * 1024:
                                raise ValueError("Avatar is too large")
                    await asyncio.to_thread(self._save_avatar, path, bytes(body))
                    return
                except (
                    httpx.HTTPError,
                    OSError,
                    ValueError,
                    SyntaxError,
                    Image.DecompressionBombError,
                ):
                    if attempt == 2:
                        raise UserError(f"头像资源未准备好：{char_id}。") from None
                    await asyncio.sleep(0.3 * (attempt + 1))

    async def close(self) -> None:
        self.closed = True
        tasks = list(self.downloads.values())
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        if self.client is not None:
            await self.client.aclose()

    def _save_avatar(self, path: Path, body: bytes) -> None:
        with Image.open(io.BytesIO(body)) as image:
            if image.format != "PNG" or max(image.size) > 2048:
                raise ValueError("Invalid avatar")
            image.load()
        temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
        try:
            temp.write_bytes(body)
            os.replace(temp, path)
        finally:
            temp.unlink(missing_ok=True)

    async def prepare(self, pool: PoolDefinition) -> None:
        await self.ensure([c for group in pool.roster.values() for c in group])

    async def render(
        self,
        pool: PoolDefinition,
        names: dict[str, str],
        results: list[DrawResult],
        mode: DrawMode,
        remaining: int,
    ) -> list[bytes]:
        shown = results if mode == DrawMode.TEN else [r for r in results if r.rarity == 6]
        self.queue_downloads([r.char_id for r in shown])
        return await asyncio.to_thread(self._render, pool, names, shown, mode, remaining)

    def _avatar(self, char_id: str) -> Image.Image:
        path = self.avatar_path(char_id)
        try:
            with Image.open(path) as image:
                if image.format != "PNG" or max(image.size) > 2048:
                    raise ValueError("Invalid avatar")
                return image.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS)
        except (
            OSError,
            ValueError,
            SyntaxError,
            UnidentifiedImageError,
            Image.DecompressionBombError,
        ):
            return self.fallback

    def _font(self, size: int):
        return ImageFont.truetype(self.font_path, size)

    @staticmethod
    def _fit(draw: ImageDraw.ImageDraw, text: str, font, width: int) -> str:
        if draw.textlength(text, font=font) <= width:
            return text
        while text and draw.textlength(text + "…", font=font) > width:
            text = text[:-1]
        return text + "…"

    def _render(self, pool, names, shown, mode, remaining) -> list[bytes]:
        avatars = {char_id: self._avatar(char_id) for char_id in {r.char_id for r in shown}}
        pages = [shown[i : i + 30] for i in range(0, len(shown), 30)] or [[]]
        if mode == DrawMode.TEN:
            pages = [shown]
        outputs = []
        title_font, body_font, small_font = self._font(28), self._font(21), self._font(16)
        for page in pages:
            is_ten = mode == DrawMode.TEN
            cols = 5 if is_ten else 6
            cell_w, cell_h, margin, top = 170, 211 if is_ten else 237, 24, 72
            card_h = 198 if is_ten else 224
            rows = max(1, (len(page) + cols - 1) // cols)
            width = cols * cell_w + margin * 2
            height = top + rows * cell_h + 60
            image = Image.new("RGB", (width, height), "#141e2a")
            draw = ImageDraw.Draw(image)
            title = f"{'十连寻访' if mode == DrawMode.TEN else '300次寻访'} · {pool.name}"
            draw.text(
                (margin, 16),
                self._fit(draw, title, title_font, width - 48),
                font=title_font,
                fill="white",
            )
            for position, result in enumerate(page):
                x = margin + position % cols * cell_w
                y = top + position // cols * cell_h
                color = COLORS[result.rarity]
                draw.rounded_rectangle(
                    (x, y, x + 158, y + card_h), radius=10, fill="#223043", outline=color, width=2
                )
                avatar = avatars[result.char_id]
                image.paste(avatar, (x + 15, y + 10), avatar)
                name = self._fit(draw, names[result.char_id], body_font, 146)
                draw.text((x + 7, y + 143), name, font=body_font, fill="white")
                stars = "★" * result.rarity
                draw.text(
                    (x + 8, y + 174),
                    stars,
                    font=small_font,
                    fill=color,
                )
                if not is_ten:
                    draw.text(
                        (x + 8, y + 200),
                        f"第{result.index}抽",
                        font=small_font,
                        fill="#d1deec",
                    )
            if not page:
                draw.text((margin, top + 40), "本次没有获得六星干员", font=title_font, fill="white")
            footer = f"今日剩余十连 {remaining} 次" if is_ten else f"六星合计 {len(shown)} 名"
            footer_y = height - 42
            draw.text((margin, footer_y), footer, font=body_font, fill="#d1deec")
            output = io.BytesIO()
            image.save(output, format="PNG")
            outputs.append(output.getvalue())
        return outputs
