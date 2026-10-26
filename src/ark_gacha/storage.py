import asyncio
import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import aiosqlite

from .models import DrawMode, DrawState, UserError


def quota_day(now: datetime) -> str:
    if now.tzinfo is None:
        raise ValueError("Clock must be timezone aware")
    return (now.astimezone(ZoneInfo("Asia/Shanghai")) - timedelta(hours=4)).date().isoformat()


@dataclass(frozen=True)
class UserSnapshot:
    revision: int
    day: str
    ten: int
    well: int
    state: DrawState


class Store:
    def __init__(self, path: Path):
        self.path = path
        self.lock = asyncio.Lock()
        self.db: aiosqlite.Connection | None = None

    async def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = await aiosqlite.connect(self.path, isolation_level=None)
        await self.db.execute("PRAGMA journal_mode=WAL")
        await self.db.execute("PRAGMA busy_timeout=5000")
        async with self.db.execute("PRAGMA user_version") as cursor:
            row = await cursor.fetchone()
            if row[0] not in (0, 1):
                await self.db.close()
                raise UserError("数据库版本不兼容，请使用对应版本程序。")
        await self.db.executescript("""
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            CREATE TABLE IF NOT EXISTS users (user_id TEXT PRIMARY KEY, revision INTEGER NOT NULL);
            CREATE TABLE IF NOT EXISTS quotas (
                user_id TEXT NOT NULL, day TEXT NOT NULL,
                ten INTEGER NOT NULL, well INTEGER NOT NULL,
                PRIMARY KEY(user_id, day),
                CHECK (ten BETWEEN 0 AND 3), CHECK (well BETWEEN 0 AND 1));
            CREATE TABLE IF NOT EXISTS pity (
                user_id TEXT NOT NULL, domain TEXT NOT NULL, misses INTEGER NOT NULL,
                PRIMARY KEY(user_id, domain));
            CREATE TABLE IF NOT EXISTS pool_states (
                user_id TEXT NOT NULL, pool_id TEXT NOT NULL, state TEXT NOT NULL,
                PRIMARY KEY(user_id, pool_id));
            CREATE TABLE IF NOT EXISTS requests (
                request_id TEXT PRIMARY KEY, user_id TEXT NOT NULL, day TEXT NOT NULL,
                mode TEXT NOT NULL, created_at REAL NOT NULL);
            PRAGMA user_version=1;
        """)

    async def close(self) -> None:
        if self.db:
            await self.db.close()
            self.db = None

    async def _one(self, sql: str, parameters: tuple = ()):
        assert self.db is not None
        async with self.db.execute(sql, parameters) as cursor:
            return await cursor.fetchone()

    async def active(self) -> dict | None:
        async with self.lock:
            row = await self._one("SELECT value FROM settings WHERE key='active_pool'")
            return json.loads(row[0]) if row else None

    async def set_active(self, pool_id: str, version: str) -> None:
        async with self.lock:
            assert self.db is not None
            await self.db.execute(
                "INSERT INTO settings VALUES ('active_pool', ?) "
                "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                (json.dumps({"pool_id": pool_id, "version": version}),),
            )

    async def seen(self, request_id: str) -> bool:
        async with self.lock:
            return bool(await self._one("SELECT 1 FROM requests WHERE request_id=?", (request_id,)))

    async def read(
        self, user: str, pool_id: str, domain: str, now: datetime, *, include_state: bool = True
    ) -> UserSnapshot:
        async with self.lock:
            revision = await self._one("SELECT revision FROM users WHERE user_id=?", (user,))
            day = quota_day(now)
            quota = await self._one(
                "SELECT ten, well FROM quotas WHERE user_id=? AND day=?", (user, day)
            )
            pool = pity = None
            if include_state:
                pool = await self._one(
                    "SELECT state FROM pool_states WHERE user_id=? AND pool_id=?", (user, pool_id)
                )
                pity = await self._one(
                    "SELECT misses FROM pity WHERE user_id=? AND domain=?", (user, domain)
                )
            state = DrawState.model_validate_json(pool[0]) if pool else DrawState()
            state.misses = pity[0] if pity else 0
            return UserSnapshot(
                revision[0] if revision else 0,
                day,
                quota[0] if quota else 0,
                quota[1] if quota else 0,
                state,
            )

    async def commit_draw(
        self,
        user: str,
        request_id: str,
        pool_id: str,
        domain: str,
        mode: DrawMode,
        before: UserSnapshot,
        after: DrawState,
        now: datetime,
    ) -> bool:
        async with self.lock:
            assert self.db is not None
            await self.db.execute("BEGIN IMMEDIATE")
            try:
                if await self._one("SELECT 1 FROM requests WHERE request_id=?", (request_id,)):
                    await self.db.rollback()
                    return False
                revision = await self._one("SELECT revision FROM users WHERE user_id=?", (user,))
                if (revision[0] if revision else 0) != before.revision:
                    raise UserError("个人状态已变化，请重新发送指令。")
                if (
                    mode == DrawMode.TEN
                    and before.ten >= 3
                    or mode == DrawMode.WELL
                    and before.well >= 1
                ):
                    raise UserError("今日该类寻访次数已用完，北京时间04:00刷新。")
                await self.db.execute(
                    "INSERT INTO users VALUES (?, ?) ON CONFLICT(user_id) "
                    "DO UPDATE SET revision=excluded.revision",
                    (user, before.revision + 1),
                )
                await self.db.execute(
                    "INSERT INTO quotas VALUES (?, ?, ?, ?) ON CONFLICT(user_id,day) "
                    "DO UPDATE SET ten=excluded.ten, well=excluded.well",
                    (
                        user,
                        before.day,
                        before.ten + (mode == DrawMode.TEN),
                        before.well + (mode == DrawMode.WELL),
                    ),
                )
                if mode == DrawMode.TEN:
                    await self.db.execute(
                        "INSERT INTO pity VALUES (?, ?, ?) ON CONFLICT(user_id,domain) "
                        "DO UPDATE SET misses=excluded.misses",
                        (user, domain, after.misses),
                    )
                    pool_state = after.model_copy(update={"misses": 0})
                    await self.db.execute(
                        "INSERT INTO pool_states VALUES (?, ?, ?) ON CONFLICT(user_id,pool_id) "
                        "DO UPDATE SET state=excluded.state",
                        (user, pool_id, pool_state.model_dump_json()),
                    )
                await self.db.execute(
                    "INSERT INTO requests VALUES (?, ?, ?, ?, ?)",
                    (request_id, user, before.day, mode.value, now.timestamp()),
                )
                await self.db.commit()
                return True
            except BaseException:
                await self.db.rollback()
                raise
