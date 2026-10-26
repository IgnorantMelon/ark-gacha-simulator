import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ark_gacha.models import DrawMode, DrawState
from ark_gacha.service import GachaService, parse_command
from ark_gacha.storage import Store, quota_day


async def send(app, text, user="u", group="g1", key=None):
    service, _ = app
    return await service.handle(user, group, key or text, text)


async def view(app, user="u", pool_id="NORMAL_TEST", domain="standard"):
    service, clock = app
    return await service.store.read(user, pool_id, domain, clock())


async def test_command_fields_and_numeric_keyword(app):
    current = await send(app, "方舟卡池")
    assert current.texts == ["测试卡池 | char_up"]
    full = await send(app, "卡池列表")
    assert full.texts == ["测试卡池 | char_up | NORMAL_TEST"]
    page = await send(app, "卡池列表 1")
    assert page.texts == full.texts
    result = await send(app, "方舟卡池 测试")
    assert result.texts == ["NORMAL_TEST | 测试卡池 | char_up"]
    numeric = await send(app, "方舟卡池 999")
    assert numeric.texts == ["没有匹配的可选卡池。"]


async def test_pool_pages_include_up_and_put_id_last(app):
    service, _ = app
    snapshot = service.catalog.load()
    original = snapshot.pools[0]
    snapshot.pools = [
        original.model_copy(update={"id": f"POOL_{i:02}", "name": f"卡池{i}", "opened_at": i + 2})
        for i in range(12)
    ]
    latest = await send(app, "卡池列表")
    page_one = await send(app, "卡池列表 1")
    page_two = await send(app, "卡池列表 2")
    assert latest.texts == page_one.texts
    assert page_one.texts == [
        "\n".join(f"卡池{i} | char_up | POOL_{i:02}" for i in range(11, 1, -1))
    ]
    assert page_two.texts == ["卡池1 | char_up | POOL_01\n卡池0 | char_up | POOL_00"]
    assert (await send(app, "方舟卡池 01")).texts == ["POOL_01 | 卡池1 | char_up"]


async def test_current_pool_without_direct_six_up(app):
    service, _ = app
    service.catalog.load().pools[0].up.clear()
    assert (await send(app, "方舟卡池")).texts == ["测试卡池 | 本期无直接UP六星干员"]
    assert (await send(app, "卡池列表 1")).texts == ["测试卡池 | 无 | NORMAL_TEST"]


@pytest.mark.parametrize("command", ["help", "/help", "帮助", "/帮助"])
async def test_help_aliases_only_list_commands_without_requiring_a_pool(app, command):
    service, _ = app
    before = await view(app)
    await service.store.db.execute("DELETE FROM settings")
    (service.catalog.root / "current").unlink()
    assert parse_command(command) == (command, "")
    reply = await send(app, command)
    assert reply.texts == [
        "方舟十连\n方舟一井\n方舟卡池\n方舟卡池 <关键词>\n"
        "卡池列表\n卡池列表 <页码>\n切换卡池 <pool_id>\n切换卡池 latest\n"
        "更新卡池\n查看水位\n查看历史\nhelp | /help | 帮助 | /帮助"
    ]
    assert not reply.images
    assert await view(app) == before
    assert await send(app, command, group="not-allowed") is None


@pytest.mark.parametrize("command", ["help", "/help", "帮助", "/帮助"])
async def test_help_does_not_accept_arguments(app, command):
    assert (await send(app, command + " extra")).texts == [f"用法：{command}"]


async def test_pity_query_only_shows_misses_and_pool_pulls(app):
    assert (await send(app, "查看水位")).texts == ["连续未出六星：0抽\n本池累计抽数：0抽"]
    await send(app, "方舟十连", key="ten")
    before = await view(app)
    expected = f"连续未出六星：{before.state.misses}抽\n本池累计抽数：10抽"
    assert (await send(app, "查看水位")).texts == [expected]
    assert await view(app) == before
    await send(app, "方舟一井", key="well")
    assert (await send(app, "查看水位")).texts == [expected]


@pytest.mark.parametrize("pool_id", ["NORMAL_TEST", "normal_test", "NoRmAl_TeSt"])
async def test_select_pool_id_is_case_insensitive(app, pool_id):
    service, _ = app
    reply = await send(app, f"切换卡池 {pool_id}", user="admin")
    assert reply.texts == ["已切换卡池：NORMAL_TEST | 测试卡池"]
    assert (await service.store.active())["pool_id"] == "NORMAL_TEST"


@pytest.mark.parametrize("keyword", ["latest", "LATEST", "LaTeSt"])
async def test_select_latest_uses_opening_time_and_stable_id_order(app, keyword):
    service, clock = app
    snapshot = service.catalog.load()
    original = snapshot.pools[0]
    now = int(clock().timestamp())
    for pool_id, name, opened_at in [
        ("Z_LATEST", "同期开池Z", now - 1),
        ("A_LATEST", "同期开池A", now - 1),
        ("FUTURE", "未开放池", now + 1),
        ("ROTATION", "适合多种场合的强力干员", now),
    ]:
        snapshot.pools.append(
            original.model_copy(update={"id": pool_id, "name": name, "opened_at": opened_at})
        )
    active = await service.store.active()
    assert "管理员" in (await send(app, f"切换卡池 {keyword}")).texts[0]
    assert await service.store.active() == active
    reply = await send(app, f"切换卡池 {keyword}", user="admin")
    assert reply.texts == ["已切换卡池：A_LATEST | 同期开池A"]
    assert await service.store.active() == {"pool_id": "A_LATEST", "version": snapshot.version}
    assert (await send(app, "方舟卡池", group="g2")).texts == ["同期开池A | char_up"]


async def test_select_latest_with_no_available_pools_keeps_active_pool(app):
    service, _ = app
    active = await service.store.active()
    service.catalog.load().pools.clear()
    reply = await send(app, "切换卡池 latest", user="admin")
    assert reply.texts == ["暂无可选卡池，请管理员先执行更新卡池。"]
    assert await service.store.active() == active


async def test_select_latest_asset_failure_keeps_active_pool(app):
    service, _ = app
    snapshot = service.catalog.load()
    snapshot.pools.append(snapshot.pools[0].model_copy(update={"id": "NEWEST", "opened_at": 2}))
    active = await service.store.active()
    service.renderer.fail = True
    reply = await send(app, "切换卡池 latest", user="admin")
    assert "处理失败" in reply.texts[0]
    assert await service.store.active() == active


@pytest.mark.parametrize("arg", ["0", "-1", "1.5", "abc", "999", "１"])
async def test_bad_page(app, arg):
    reply = await send(app, f"卡池列表 {arg}")
    assert "范围" in reply.texts[0]


@pytest.mark.parametrize(
    "old",
    [
        "方舟卡池列表",
        "方舟搜池 a",
        "方舟切池 a",
        "方舟切换卡池 a",
        "方舟更新卡池",
        "方舟水位",
        "方舟次数",
        "方舟重发",
    ],
)
def test_removed_commands_not_registered(old):
    assert parse_command(old) is None


async def test_auth_and_group_allowlist(app):
    assert await send(app, "方舟十连", group="not-allowed") is None
    reply = await send(app, "切换卡池 NORMAL_TEST")
    assert "管理员" in reply.texts[0]
    reply = await send(app, "切换卡池 NORMAL_TEST", user="admin")
    assert "已切换" in reply.texts[0]


async def test_legacy_rotation_not_listed_searchable_or_selectable(app):
    service, _ = app
    snapshot = service.catalog.load()
    snapshot.pools.append(
        snapshot.pools[0].model_copy(
            update={
                "id": "ROTATION",
                "name": "适合多种场合的强力干员",
            }
        )
    )
    assert (await send(app, "卡池列表")).texts == ["测试卡池 | char_up | NORMAL_TEST"]
    assert (await send(app, "卡池列表 1")).texts == ["测试卡池 | char_up | NORMAL_TEST"]
    for keyword in ["适合多种场合", "ROTATION"]:
        assert (await send(app, f"方舟卡池 {keyword}")).texts == ["没有匹配的可选卡池。"]
    reply = await send(app, "切换卡池 ROTATION", user="admin")
    assert "不存在" in reply.texts[0]
    assert (await service.store.active())["pool_id"] == "NORMAL_TEST"


async def test_legacy_active_rotation_blocks_new_draws_without_charging(app):
    service, _ = app
    snapshot = service.catalog.load()
    snapshot.pools.append(
        snapshot.pools[0].model_copy(
            update={
                "id": "ROTATION",
                "name": "适合多种场合的强力干员",
            }
        )
    )
    await service.store.set_active("ROTATION", snapshot.version)
    for command in ["方舟卡池", "查看水位", "方舟十连", "方舟一井"]:
        reply = await send(app, command)
        assert "专属名称" in reply.texts[0]
        assert not reply.images
    assert (await view(app)).ten == 0 and (await view(app)).well == 0
    assert not service.history


async def test_no_active_pool_and_generation_failure_do_not_charge(app):
    service, _ = app
    service.renderer.fail = True
    reply = await send(app, "方舟十连")
    assert reply.texts and (await view(app)).ten == 0
    service.renderer.fail = False
    await service.store.db.execute("DELETE FROM settings")
    reply = await send(app, "方舟十连")
    assert "尚未选择" in reply.texts[0] and (await view(app)).ten == 0


async def test_cross_group_limit_and_concurrency(app):
    replies = await asyncio.gather(
        *(send(app, "方舟十连", group="g1" if i % 2 else "g2", key=str(i)) for i in range(10))
    )
    assert sum(bool(reply.images) for reply in replies) == 3
    assert (await view(app)).ten == 3
    assert (await view(app)).state.pulls == 30
    assert (await send(app, "方舟一井", key="well")).images
    assert "次数已用完" in (await send(app, "方舟一井", key="well2")).texts[0]


async def test_duplicate_event_is_not_charged_twice(app):
    replies = await asyncio.gather(*(send(app, "方舟十连", key="same") for _ in range(8)))
    assert sum(reply is not None for reply in replies) == 1
    assert (await view(app)).ten == 1


async def test_history_latest_cross_group_expiry_no_extension(app):
    service, clock = app
    first = await send(app, "方舟十连", key="first")
    clock.now += timedelta(seconds=100)
    last = await send(app, "方舟十连", key="second")
    assert first.images != last.images  # remaining count differs even if RNG happens to match
    before = await view(app)
    await send(app, "方舟一井", key="well")
    assert (await send(app, "查看历史", group="g2")).images == last.images
    assert (await view(app)).state == before.state
    clock.now += timedelta(seconds=599)
    assert (await send(app, "查看历史")).images == last.images
    clock.now += timedelta(seconds=1)
    assert "暂无" in (await send(app, "查看历史")).texts[0]
    assert not service.history
    assert (await view(app)).ten == 2


async def test_well_starts_fresh_and_does_not_store_results(app):
    service, _ = app
    await send(app, "方舟十连", key="ten")
    before = await view(app)
    from ark_gacha.engine import simulate

    inputs = []

    def capture(pool, initial, count):
        inputs.append(initial.model_copy(deep=True))
        return simulate(pool, initial, count)

    service.simulator = capture
    await send(app, "方舟一井", key="well")
    assert inputs == [DrawState()]
    after = await view(app)
    assert after.state == before.state and after.well == 1
    assert service.history["u"].images
    async with service.store.db.execute("PRAGMA table_info(requests)") as cur:
        columns = [row[1] for row in await cur.fetchall()]
    assert columns == ["request_id", "user_id", "day", "mode", "created_at"]


async def test_reset_at_four_and_persist_after_restart(app):
    service, clock = app
    clock.now = datetime(2026, 10, 26, 19, 59, 59, tzinfo=UTC)  # Oct 27 03:59:59 Beijing
    for i in range(3):
        await send(app, "方舟十连", key=str(i))
    old_state = (await view(app)).state
    clock.now += timedelta(seconds=1)
    before = await view(app)
    assert before.ten == 0 and before.state == old_state
    await service.store.close()
    await service.store.open()
    restarted = GachaService(
        service.settings, service.store, service.catalog, service.renderer, clock
    )
    assert (await restarted.store.read("u", "NORMAL_TEST", "standard", clock())).state == old_state
    assert "暂无" in (await restarted.handle("u", "g1", "history", "查看历史")).texts[0]


async def test_pool_pity_domains_and_returning_old_progress(app):
    service, clock = app
    await send(app, "方舟十连", key="p1")
    first = await view(app)
    other = await view(app, pool_id="OTHER", domain="standard")
    assert other.state.misses == first.state.misses and other.state.pulls == 0
    assert (await view(app, pool_id="KERNEL", domain="kernel")).state == DrawState()
    assert (await view(app, pool_id="LIMITED", domain="LIMITED")).state == DrawState()
    clock.now += timedelta(days=1)
    await send(app, "方舟十连", key="p2")
    assert (await view(app)).state.pulls == 20


async def test_actual_switches_share_only_the_correct_pity_and_keep_history(app):
    from ark_gacha.engine import simulate

    class HighRoll:
        def random(self):
            return 0.999999

    service, clock = app
    service.simulator = lambda pool, state, count: simulate(pool, state, count, HighRoll())
    snapshot = service.catalog.load()
    original = snapshot.pools[0]
    for pool_id, domain in [("OTHER", "standard"), ("KERNEL", "kernel"), ("LIMITED", "LIMITED")]:
        snapshot.pools.append(original.model_copy(update={"id": pool_id, "pity_domain": domain}))
    first = await send(app, "方舟十连", key="first")
    assert (await view(app)).state.misses == 10
    await send(app, "切换卡池 OTHER", user="admin")
    assert (await send(app, "查看历史")).images == first.images
    await send(app, "方舟十连", group="g2", key="other")
    assert (await view(app, pool_id="OTHER")).state.misses == 20
    await send(app, "切换卡池 KERNEL", user="admin")
    await send(app, "方舟十连", key="kernel")
    assert (await view(app, pool_id="KERNEL", domain="kernel")).state.misses == 10
    clock.now += timedelta(days=1)
    await send(app, "切换卡池 LIMITED", user="admin")
    await send(app, "方舟十连", key="limited")
    assert (await view(app, pool_id="LIMITED", domain="LIMITED")).state.misses == 10
    await send(app, "切换卡池 NORMAL_TEST", user="admin")
    returned = await view(app)
    assert returned.state.pulls == 10 and returned.state.misses == 20
    assert (await send(app, "查看水位")).texts == ["连续未出六星：20抽\n本池累计抽数：10抽"]


async def test_optimistic_transaction_prevents_cross_process_lost_update(app):
    service, clock = app
    second = Store(service.store.path)
    await second.open()
    try:
        initial = await view(app)
        await service.store.commit_draw(
            "u",
            "one",
            "NORMAL_TEST",
            "standard",
            DrawMode.TEN,
            initial,
            DrawState(pulls=10),
            clock(),
        )
        with pytest.raises(Exception, match="状态已变化"):
            await second.commit_draw(
                "u",
                "two",
                "NORMAL_TEST",
                "standard",
                DrawMode.TEN,
                initial,
                DrawState(pulls=10),
                clock(),
            )
        assert (await view(app)).ten == 1
    finally:
        await second.close()


def test_beijing_quota_day():
    assert quota_day(datetime(2026, 10, 26, 19, 59, 59, tzinfo=UTC)) == "2026-10-26"
    assert quota_day(datetime(2026, 10, 26, 20, tzinfo=UTC)) == "2026-10-27"
