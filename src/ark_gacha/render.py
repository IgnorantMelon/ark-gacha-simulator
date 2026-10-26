import asyncio
import io
import os
import re
import uuid
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, UnidentifiedImageError

from .models import DrawMode, DrawResult, PoolDefinition, UserError

AVATAR_ROOT = "https://raw.githubusercontent.com/yuanyan3060/ArknightsGameResource/main/avatar"
COLORS = {3: "#83a8c0", 4: "#aa93de", 5: "#f3c45d", 6: "#ff9c43"}


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
        self.prepare_lock = asyncio.Lock()

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
            return True
        except (OSError, ValueError, UnidentifiedImageError):
            return False

    async def ensure(self, characters: list[str]) -> None:
        async with self.prepare_lock:
            async with httpx.AsyncClient(timeout=25, follow_redirects=True) as client:

                async def download(char_id):
                    path = self.avatar_path(char_id)
                    if await asyncio.to_thread(self.valid_image, path):
                        return
                    async with self.download_limit:
                        for attempt in range(3):
                            try:
                                async with client.stream(
                                    "GET", f"{AVATAR_ROOT}/{char_id}.png"
                                ) as r:
                                    r.raise_for_status()
                                    body = bytearray()
                                    async for chunk in r.aiter_bytes():
                                        body.extend(chunk)
                                        if len(body) > 4 * 1024 * 1024:
                                            raise ValueError("Avatar is too large")
                                await asyncio.to_thread(self._save_avatar, path, bytes(body))
                                return
                            except (httpx.HTTPError, OSError, ValueError):
                                if attempt == 2:
                                    raise UserError(
                                        f"头像资源未准备好：{char_id}，本次未扣次数。"
                                    ) from None
                                await asyncio.sleep(0.3 * (attempt + 1))

                # return_exceptions ensures all download tasks finish before closing the client.
                results = await asyncio.gather(
                    *(download(c) for c in sorted(set(characters))), return_exceptions=True
                )
                for result in results:
                    if isinstance(result, BaseException):
                        raise result

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
        await self.ensure([r.char_id for r in shown])
        return await asyncio.to_thread(self._render, pool, names, shown, mode, remaining)

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
        pages = [shown[i : i + 30] for i in range(0, len(shown), 30)] or [[]]
        if mode == DrawMode.TEN:
            pages = [shown]
        outputs = []
        title_font, body_font, small_font = self._font(28), self._font(21), self._font(16)
        for page in pages:
            is_ten = mode == DrawMode.TEN
            cols = 5 if is_ten else 6
            cell_w, cell_h, margin, top = 170, 211, 24, 72
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
                    (x, y, x + 158, y + 198), radius=10, fill="#223043", outline=color, width=2
                )
                with Image.open(self.avatar_path(result.char_id)) as avatar:
                    avatar = avatar.convert("RGBA").resize((128, 128), Image.Resampling.LANCZOS)
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
            if not page:
                draw.text((margin, top + 40), "本次没有获得六星干员", font=title_font, fill="white")
            footer = f"今日剩余十连 {remaining} 次" if is_ten else f"六星合计 {len(shown)} 名"
            footer_y = height - 42
            draw.text((margin, footer_y), footer, font=body_font, fill="#d1deec")
            output = io.BytesIO()
            image.save(output, format="PNG")
            outputs.append(output.getvalue())
        return outputs
