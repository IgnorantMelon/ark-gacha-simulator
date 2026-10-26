import json
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from ark_gacha.catalog import Catalog
from ark_gacha.config import Settings
from ark_gacha.models import CatalogSnapshot, PoolDefinition, RuleProfile, UpGroup
from ark_gacha.service import GachaService
from ark_gacha.storage import Store


@pytest.fixture
def pool():
    return PoolDefinition(
        id="NORMAL_TEST",
        name="测试卡池",
        opened_at=1,
        closed_at=2,
        rule_type="NORMAL",
        pity_domain="standard",
        rates={6: 0.02, 5: 0.08, 4: 0.5, 3: 0.4},
        roster={
            6: ["char_up", "char_off"],
            5: ["char_fivea", "char_fiveb", "char_fiveoff"],
            4: ["char_four"],
            3: ["char_three"],
        },
        up=[
            UpGroup(rarity=6, characters=["char_up"], per_character=0.5),
            UpGroup(rarity=5, characters=["char_fivea", "char_fiveb"], per_character=0.25),
        ],
        rules=RuleProfile(),
        evidence="fixture",
    )


class Clock:
    now = datetime(2026, 10, 26, tzinfo=UTC)

    def __call__(self):
        return self.now


class FakeRenderer:
    fail = False

    async def prepare(self, pool):
        if self.fail:
            raise OSError("fixture failure")

    async def render(self, pool, names, results, mode, remaining):
        await self.prepare(pool)
        payload = [pool.id, mode.value, remaining, [r.char_id for r in results]]
        return [json.dumps(payload).encode()]


@pytest_asyncio.fixture
async def app(tmp_path, pool):
    catalog = Catalog(tmp_path / "catalog")
    catalog.root.mkdir()
    snapshot = CatalogSnapshot(
        version="0123456789abcdef",
        fetched_at="2026-10-26",
        sources={},
        operators={c: c for cs in pool.roster.values() for c in cs},
        pools=[pool],
        excluded=[],
    )
    catalog._snapshots[snapshot.version] = snapshot
    (catalog.root / "current").write_text(snapshot.version)
    store = Store(tmp_path / "bot.sqlite3")
    await store.open()
    await store.set_active(pool.id, snapshot.version)
    clock = Clock()
    service = GachaService(
        Settings(
            _env_file=None, allowed_groups={"g1", "g2"}, admin_qq={"admin"}, data_dir=tmp_path
        ),
        store,
        catalog,
        FakeRenderer(),
        clock,
    )
    yield service, clock
    await store.close()
