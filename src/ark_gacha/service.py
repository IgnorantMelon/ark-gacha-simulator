import asyncio
import logging
import math
import weakref
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .catalog import Catalog, is_named_pool
from .config import Settings
from .engine import simulate, six_rate
from .models import DrawMode, DrawState, UserError
from .render import Renderer
from .storage import Store

log = logging.getLogger(__name__)
COMMANDS = {
    "方舟十连",
    "方舟一井",
    "方舟卡池",
    "卡池列表",
    "切换卡池",
    "更新卡池",
    "查看水位",
    "查看历史",
}


@dataclass
class Reply:
    texts: list[str] = field(default_factory=list)
    images: list[bytes] = field(default_factory=list)


@dataclass(frozen=True)
class TenHistory:
    generated_at: datetime
    pool_id: str
    pool_name: str
    images: tuple[bytes, ...]


def parse_command(text: str) -> tuple[str, str] | None:
    parts = text.strip().split(maxsplit=1)
    if not parts or parts[0] not in COMMANDS:
        return None
    return parts[0], parts[1].strip() if len(parts) > 1 else ""


class GachaService:
    def __init__(
        self,
        settings: Settings,
        store: Store,
        catalog: Catalog,
        renderer: Renderer,
        clock: Callable[[], datetime] | None = None,
        simulator=simulate,
    ):
        self.settings, self.store, self.catalog, self.renderer = settings, store, catalog, renderer
        self.clock = clock or (lambda: datetime.now(UTC))
        self.simulator = simulator
        self.history: dict[str, TenHistory] = {}
        self.user_locks: weakref.WeakValueDictionary[str, asyncio.Lock] = (
            weakref.WeakValueDictionary()
        )

    def user_lock(self, user: str) -> asyncio.Lock:
        lock = self.user_locks.get(user)
        if lock is None:
            lock = asyncio.Lock()
            self.user_locks[user] = lock
        return lock

    def prune_history(self) -> None:
        now = self.clock()
        for user, record in list(self.history.items()):
            if (now - record.generated_at).total_seconds() >= 600:
                del self.history[user]

    async def current(self):
        active = await self.store.active()
        if active is None:
            raise UserError("尚未选择卡池，请管理员使用切换卡池 <pool_id>。")
        snapshot = self.catalog.load(active["version"])
        pool = next((p for p in snapshot.pools if p.id == active["pool_id"]), None)
        if pool is None or pool.opened_at > self.clock().timestamp():
            raise UserError("当前卡池不可用，请管理员重新选择。")
        if not is_named_pool(pool.name):
            raise UserError("当前卡池没有专属名称，已不再支持，请管理员使用切换卡池 <pool_id>。")
        return snapshot, pool

    async def handle(self, user: str, group: str, request_id: str, text: str) -> Reply | None:
        if group not in self.settings.allowed_groups:
            return None
        parsed = parse_command(text)
        if parsed is None:
            return None
        try:
            command, arg = parsed
            if command in {"方舟十连", "方舟一井", "查看水位", "查看历史", "更新卡池"} and arg:
                raise UserError(f"用法：{command}")
            if command in {"切换卡池", "更新卡池"}:
                if user not in self.settings.admin_qq:
                    raise UserError("此命令仅配置的机器人管理员可用。")
            if command == "方舟卡池":
                return await self.pool_query(arg)
            if command == "卡池列表":
                return self.pool_list(arg)
            if command == "切换卡池":
                return await self.select_pool(arg)
            if command == "更新卡池":
                if self.catalog.sync_lock.locked():
                    raise UserError("卡池更新正在进行，请稍后再试。")
                old_ids = set()
                try:
                    old_ids = {p.id for p in self.catalog.load().pools}
                except UserError:
                    pass
                report = await self.catalog.sync()
                added = sum(p["id"] not in old_ids for p in report["pools"])
                failed = sum(not e["reason"].startswith("范围外") for e in report["excluded"])
                excluded = len(report["excluded"]) - failed
                return Reply(
                    texts=[
                        f"更新完成：可用规则 {report['supported']} 池，新增 {added}，"
                        f"失败 {failed}，范围外 {excluded}。当前选池未改变。"
                    ]
                )
            if command == "查看水位":
                _, pool = await self.current()
                before = await self.store.read(user, pool.id, pool.pity_domain, self.clock())
                state = before.state
                lines = [
                    f"连续未出六星：{state.misses}抽",
                    f"下一抽六星概率：{six_rate(pool.rates[6], state.misses):.0%}",
                    f"本池十连累计：{state.pulls}抽",
                ]
                if pool.rules.first_ten:
                    lines.append(
                        f"首次十抽五星以上保底：{'已完成' if state.first_five_done else '未完成'}"
                    )
                if pool.rules.directed_threshold:
                    lines.append(
                        f"定向选调连续未出目标：{state.directed_misses}抽；"
                        f"{'已生效' if state.directed_done else '未生效'}"
                    )
                if pool.rules.selection_thresholds:
                    lines.append(f"双UP选调已生效：{len(state.selection_picks)}次")
                if pool.rules.target_six:
                    lines.append(
                        f"联动目标连续未出：{state.target_six_misses}抽；"
                        f"保底类型：{pool.rules.target_six_mode}"
                    )
                return Reply(texts=["\n".join(lines)])
            if command == "查看历史":
                self.prune_history()
                record = self.history.get(user)
                if not record:
                    raise UserError("暂无十分钟内有效的十连历史。")
                return Reply(images=list(record.images))
            return await self.draw(
                user, request_id, DrawMode.TEN if command == "方舟十连" else DrawMode.WELL
            )
        except UserError as exc:
            return Reply(texts=[str(exc)])
        except Exception as exc:
            # Never include event contents, credentials, draw lists or exception messages.
            log.error("Command failed (%s)", type(exc).__name__)
            return Reply(texts=["处理失败，请稍后再试；未成功生成并提交的寻访不会扣次数。"])

    async def pool_query(self, arg: str) -> Reply:
        if not arg:
            snapshot, pool = await self.current()
            return Reply(
                texts=[
                    " / ".join(snapshot.operators[c] for c in pool.direct_up(6))
                    or "本期无直接UP六星干员"
                ]
            )
        snapshot = self.catalog.load()
        key = arg.casefold()
        pools = [
            p
            for p in self.catalog.available(snapshot, self.clock())
            if key in p.id.casefold() or key in p.name.casefold()
        ]
        return self.pool_rows(snapshot, pools, show_up=True)

    def pool_list(self, arg: str) -> Reply:
        snapshot = self.catalog.load()
        pools = self.catalog.available(snapshot, self.clock())
        pages = max(1, math.ceil(len(pools) / 10))
        if arg and (not arg.isascii() or not arg.isdecimal() or int(arg) < 1):
            raise UserError(f"页码必须为正整数，有效范围1至{pages}。")
        page = int(arg) if arg else 1
        if page > pages:
            raise UserError(f"页码超出范围，有效范围1至{pages}。")
        return self.pool_rows(snapshot, pools[(page - 1) * 10 : page * 10], show_up=not arg)

    @staticmethod
    def pool_rows(snapshot, pools, show_up: bool) -> Reply:
        if not pools:
            return Reply(texts=["没有匹配的可选卡池。"])
        rows = []
        for pool in pools:
            row = f"{pool.id} | {pool.name}"
            if show_up:
                row += " | " + (
                    " / ".join(snapshot.operators[c] for c in pool.direct_up(6)) or "无"
                )
            rows.append(row)
        return Reply(texts=["\n".join(rows[i : i + 10]) for i in range(0, len(rows), 10)])

    async def select_pool(self, pool_id: str) -> Reply:
        if not pool_id:
            raise UserError("用法：切换卡池 <pool_id>")
        snapshot = self.catalog.load()
        pool = next(
            (p for p in self.catalog.available(snapshot, self.clock()) if p.id == pool_id), None
        )
        if pool is None:
            raise UserError("卡池不存在、尚未开放或未通过规则校验。")
        await self.renderer.prepare(pool)
        await self.store.set_active(pool.id, snapshot.version)
        return Reply(texts=[f"已切换卡池：{pool.id} | {pool.name}"])

    async def draw(self, user: str, request_id: str, mode: DrawMode) -> Reply | None:
        async with self.user_lock(user):
            if await self.store.seen(request_id):
                return None
            snapshot, pool = await self.current()
            accepted_at = self.clock()
            before = await self.store.read(
                user, pool.id, pool.pity_domain, accepted_at, include_state=mode == DrawMode.TEN
            )
            used, limit = (before.ten, 3) if mode == DrawMode.TEN else (before.well, 1)
            if used >= limit:
                raise UserError("今日该类寻访次数已用完，北京时间04:00刷新。")
            initial = before.state if mode == DrawMode.TEN else DrawState()
            results, after = self.simulator(pool, initial, 10 if mode == DrawMode.TEN else 300)
            images = await self.renderer.render(
                pool, snapshot.operators, results, mode, limit - used - 1
            )
            if not images:
                raise UserError("图片生成失败，本次未扣次数。")
            generated_at = self.clock()
            committed = await self.store.commit_draw(
                user, request_id, pool.id, pool.pity_domain, mode, before, after, generated_at
            )
            if not committed:
                return None
            if mode == DrawMode.TEN:
                self.prune_history()
                self.history[user] = TenHistory(generated_at, pool.id, pool.name, tuple(images))
            return Reply(images=images)
