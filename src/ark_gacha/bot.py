import asyncio
import logging
from contextlib import suppress

import nonebot
from nonebot import on_message
from nonebot.adapters.onebot.v11 import Adapter, Bot, GroupMessageEvent, MessageSegment
from nonebot.rule import Rule

from .catalog import Catalog
from .config import Settings
from .render import Renderer
from .service import GachaService, parse_command
from .storage import Store

log = logging.getLogger(__name__)


def run_bot() -> None:
    settings = Settings()
    if not settings.onebot_token.strip():
        raise SystemExit("请在.env中配置非空ARK_ONEBOT_TOKEN，并在NapCat中使用相同Token。")
    if settings.host not in {"127.0.0.1", "::1", "localhost"}:
        raise SystemExit("本机部署仅允许监听回环地址。")
    store = Store(settings.database)
    service = GachaService(
        settings,
        store,
        Catalog(settings.data_dir / "catalog"),
        Renderer(settings.data_dir / "avatars", settings.font_path),
    )
    # NoneBot's INFO event logging includes incoming message text; suppress it.
    nonebot.init(
        driver="~fastapi",
        host=settings.host,
        port=settings.port,
        onebot_access_token=settings.onebot_token,
        log_level="WARNING",
    )
    driver = nonebot.get_driver()
    driver.register_adapter(Adapter)
    cleanup_task = None

    @driver.on_startup
    async def startup():
        nonlocal cleanup_task
        await store.open()

        async def cleanup():
            while True:
                await asyncio.sleep(30)
                service.prune_history()

        cleanup_task = asyncio.create_task(cleanup())

    @driver.on_shutdown
    async def shutdown():
        if cleanup_task:
            cleanup_task.cancel()
            with suppress(asyncio.CancelledError):
                await cleanup_task
        service.history.clear()
        await store.close()

    async def is_command(event: GroupMessageEvent) -> bool:
        return (
            str(event.group_id) in settings.allowed_groups
            and parse_command(event.get_plaintext()) is not None
        )

    matcher = on_message(rule=Rule(is_command), priority=10, block=True)

    @matcher.handle()
    async def handle(bot: Bot, event: GroupMessageEvent):
        if str(event.user_id) == bot.self_id:
            return
        key = f"{bot.self_id}:{event.group_id}:{event.message_id}"
        reply = await service.handle(
            str(event.user_id), str(event.group_id), key, event.get_plaintext()
        )
        if reply is None:
            return
        try:
            for text in reply.texts:
                await bot.send(event, text)
            for content in reply.images:
                await bot.send(event, MessageSegment.image(content))
        except Exception as exc:
            # Draws are already committed. Only ten-pulls have an in-memory history.
            log.error("QQ delivery failed (%s)", type(exc).__name__)

    nonebot.run()
