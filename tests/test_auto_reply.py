import asyncio
import logging
import threading
import time
from importlib.resources import files
from types import SimpleNamespace

import pytest

from ark_gacha import auto_reply
from ark_gacha.auto_reply import AutoReplyConfigError, AutoReplyService, load_rules, parse_rules
from ark_gacha.cli import main


def config(*rules, enabled=True, interval=0.1, cooldown=0):
    return (
        f"version = 1\nenabled = {str(enabled).lower()}\n"
        f"reload_interval_seconds = {interval}\ndefault_cooldown_seconds = {cooldown}\n"
        + ("\n".join("[[rules]]\n" + rule for rule in rules) if rules else "rules = []\n")
    ).encode("utf-8")


def rule(rule_id="hello", pattern="你好", reply="您好", extra=""):
    return f"id = '{rule_id}'\npattern = '{pattern}'\nreply = '{reply}'\n{extra}\n"


async def service(tmp_path, body, *, allowed=None, clock=time.monotonic):
    path = tmp_path / "auto-replies.toml"
    path.write_bytes(body)
    engine = AutoReplyService(path, {"100", "200"} if allowed is None else allowed, clock)
    assert await engine.reload()
    return engine


async def send(
    engine, text="你好", *, message="1", bot="999", group="100", user="123", at_self=False
):
    return await engine.reply_for(bot, group, user, message, text, at_self=at_self)


def test_bundled_template_and_bom():
    body = files("ark_gacha").joinpath("assets/auto-replies.example.toml").read_bytes()
    ruleset = parse_rules(b"\xef\xbb\xbf" + body)
    assert not ruleset.enabled
    assert [item.id for item in ruleset.rules] == ["sl", "who-is"]
    assert ruleset.rules[0].cooldown_seconds == 30
    assert ruleset.rules[1].cooldown_seconds == 30
    assert all(item.reply == "" for item in ruleset.rules)
    assert not ruleset.rules[0].require_at_self
    assert ruleset.rules[1].require_at_self


@pytest.mark.parametrize(
    ("body", "error"),
    [
        (b"broken = [", "TOML"),
        (b"\xff", "UTF-8"),
        (config(rule()).replace(b"version = 1", b"version = true"), "version"),
        (config(rule()).replace(b"version = 1", b"version = 2"), "version"),
        (config(rule()).replace(b"enabled = true", b'enabled = "true"', 1), "enabled"),
        (config(rule(pattern="[")), "hello.*正则"),
        (config(rule(), rule()), "重复.*hello"),
        (config(rule(extra="require_at_self = 'true'")), "hello.*require_at_self"),
        (config(rule(extra="mode = 'match'")), "hello.*mode"),
        (config(rule(extra="groups = [100]")), "hello.*groups"),
        (config(rule(extra="groups = ['']")), "hello.*groups"),
        (config(rule(extra="cooldown_seconds = -1")), "hello.*cooldown_seconds"),
        (config(rule(extra="cooldown_seconds = nan")), "hello.*cooldown_seconds"),
        (config(rule(extra="typo = true")), "hello.*typo"),
        (config(rule(), cooldown=-1), "default_cooldown_seconds"),
        (config(rule(), interval=0), "reload_interval_seconds"),
        (config(rule(pattern="[", extra="enabled = false")), "hello.*正则"),
        (config(rule(pattern="(" * 700 + "a" + ")" * 700)), "hello.*正则"),
        (b"version = 1\nrules = ['not a table']", "rules.0"),
    ],
)
def test_invalid_configuration_has_actionable_error(body, error):
    with pytest.raises(AutoReplyConfigError, match=error):
        parse_rules(body)


def test_file_size_limit(tmp_path):
    path = tmp_path / "huge.toml"
    path.write_bytes(b"#" * (auto_reply.MAX_FILE_BYTES + 1))
    with pytest.raises(AutoReplyConfigError, match="256 KiB"):
        load_rules(path)


async def test_match_modes_order_scopes_and_disabled_rules(tmp_path):
    engine = await service(
        tmp_path,
        config(
            rule("disabled", extra="enabled = false"),
            rule("scoped", extra="groups = ['200']"),
            rule("exact", pattern=r"早\s*安[！!]*", extra="mode = 'fullmatch'\nat_sender = true"),
            rule("partial", pattern=r"(?i)hello|你好"),
        ),
    )
    assert (await send(engine, "  早 安！\n")).id == "exact"
    assert (await send(engine, "HELLO, world", message="2")).id == "partial"
    assert (await send(engine, "你好啊", message="3", group="200")).id == "scoped"
    assert (await send(engine, "你好啊", message="4")).id == "partial"
    assert await send(engine, "大家早安", message="5") is None
    assert await send(engine, "只有图片以外无文字", message="6") is None
    assert await send(engine, "  ", message="7") is None
    assert await send(engine, "你好" * auto_reply.MAX_TEXT_LENGTH, message="8") is None
    assert await send(engine, group="300") is None
    assert await send(engine, user="999") is None
    engine.path.write_bytes(config(rule(), enabled=False))
    assert await engine.reload()
    assert await send(engine, message="9") is None


async def test_empty_allowlist_cannot_be_extended_by_rules(tmp_path):
    engine = await service(tmp_path, config(rule(extra="groups = ['100']")), allowed=set())
    assert await send(engine) is None


async def test_blank_replies_are_drafts_and_filling_them_activates_matching(tmp_path):
    engine = await service(
        tmp_path,
        config(rule("empty", reply=""), rule("spaces", reply="  ")),
    )
    assert await send(engine) is None
    assert not engine._cooldowns
    engine.path.write_bytes(config(rule("empty", reply=""), rule("filled")))
    assert await engine.reload()
    assert (await send(engine, message="2")).id == "filled"


async def test_sl_fullmatch_and_who_requires_at_self(tmp_path):
    body = files("ark_gacha").joinpath("assets/auto-replies.example.toml").read_bytes()
    body = body.replace(b"enabled = false", b"enabled = true", 1)
    body = body.replace(b"default_cooldown_seconds = 30", b"default_cooldown_seconds = 0")
    body = body.replace(b'reply = ""', b'reply = "fixture reply"')
    engine = await service(tmp_path, body)
    assert (await send(engine, "#sl")).id == "sl"
    assert await send(engine, "#sl extra", message="2") is None
    assert await send(engine, "prefix#sl", message="3") is None
    assert await send(engine, "#SL", message="4") is None
    assert await send(engine, "你是谁", message="5") is None
    assert (await send(engine, "请问你是谁？", message="6", at_self=True)).id == "who-is"
    assert await send(engine, "你好", message="7", at_self=True) is None


async def test_cooldown_is_per_bot_group_rule_and_first_match_does_not_fall_through(tmp_path):
    now = [100.0]
    engine = await service(
        tmp_path,
        config(rule("first"), rule("fallback"), cooldown=10),
        clock=lambda: now[0],
    )
    assert (await send(engine)).id == "first"
    assert await send(engine, message="2", user="456") is None
    assert (await send(engine, group="200")).id == "first"
    assert (await send(engine, bot="998")).id == "first"
    now[0] += 10
    assert (await send(engine, message="3")).id == "first"
    assert await send(engine, message="1") is None  # dedup outlives cooldown
    now[0] += auto_reply.DEDUP_SECONDS
    assert await send(engine, message="1") is not None


async def test_concurrent_duplicate_and_cooldown_reservations(tmp_path):
    engine = await service(tmp_path, config(rule(), cooldown=10))
    results = await asyncio.gather(send(engine), send(engine), send(engine, message="2"))
    assert sum(result is not None for result in results) == 1
    engine = await service(tmp_path, config(rule(), cooldown=0))
    assert await send(engine) is not None
    assert await send(engine) is None
    assert await send(engine, message="2") is not None


async def test_reload_transaction_errors_recovery_cooldown_and_shutdown(tmp_path, caplog):
    now = [100.0]
    engine = await service(tmp_path, config(rule(), cooldown=10), clock=lambda: now[0])
    assert await send(engine) is not None
    previous = engine.ruleset
    engine.path.write_bytes(config(rule(pattern="[")))
    with caplog.at_level(logging.WARNING):
        assert not await engine.reload()
        assert not await engine.reload()
    assert engine.ruleset is previous
    assert sum("reload failed" in item.message for item in caplog.records) == 1
    assert "hello" in caplog.text
    engine.path.unlink()
    assert not await engine.reload()
    assert engine.ruleset is previous
    engine.path.write_bytes(config(rule(reply="更新后的回复"), cooldown=10))
    assert await engine.reload()
    assert await send(engine, message="2") is None  # no cooldown reset after editing
    now[0] += 10
    assert (await send(engine, message="3")).reply == "更新后的回复"
    assert await send(engine) is None  # reload does not reset dedup
    await engine.close()
    assert await send(engine, message="4") is None
    await engine.start()
    try:
        assert (await send(engine, message="4")).reply == "更新后的回复"
    finally:
        await engine.close()


async def test_invalid_or_missing_startup_leaves_feature_disabled_and_recovers(tmp_path):
    engine = AutoReplyService(tmp_path / "auto-replies.toml", {"100"})
    assert not await engine.reload()
    assert not engine.ruleset.enabled
    engine.path.write_bytes(b"not valid TOML")
    assert not await engine.reload()
    assert await send(engine) is None
    engine.path.write_bytes(config(rule()))
    assert await engine.reload()
    assert await send(engine) is not None


async def test_file_watcher_applies_changes_without_message_traffic(tmp_path, monkeypatch):
    engine = await service(tmp_path, config(rule()))
    loaded = asyncio.Event()
    original = engine.reload

    async def observed_reload():
        changed = await original()
        if changed:
            loaded.set()
        return changed

    monkeypatch.setattr(engine, "reload", observed_reload)
    await engine.start()
    try:
        engine.path.write_bytes(config(rule(reply="新回复"), enabled=False))
        await asyncio.wait_for(loaded.wait(), 2)
        assert not engine.ruleset.enabled
        assert engine.ruleset.rules[0].reply == "新回复"
    finally:
        await engine.close()
    assert engine._watcher is None


async def test_reload_while_matching_drops_stale_reply_and_does_not_block_loop(
    tmp_path, monkeypatch
):
    engine = await service(tmp_path, config(rule()))
    started, release = asyncio.Event(), threading.Event()
    loop = asyncio.get_running_loop()
    original = auto_reply.first_match

    def delayed(*args):
        loop.call_soon_threadsafe(started.set)
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(auto_reply, "first_match", delayed)
    pending = asyncio.create_task(send(engine))
    try:
        await asyncio.wait_for(started.wait(), 1)
        engine.path.write_bytes(config(rule(), enabled=False))
        assert await engine.reload()
    finally:
        release.set()
    assert await pending is None


async def test_real_regex_timeout_aborts_message_and_next_message_still_works(tmp_path, caplog):
    engine = await service(
        tmp_path,
        config(rule("expensive", "(a|aa)+$"), rule("fallback", "a"), rule()),
    )
    started = time.monotonic()
    assert await asyncio.wait_for(send(engine, "a" * 2000 + "!"), 2) is None
    assert time.monotonic() - started < 1
    assert "timed out (rule=expensive)" in caplog.text
    assert (await send(engine, message="2")).id == "hello"
    assert "a" * 100 not in caplog.text


def test_whole_message_budget_aborts_even_when_each_pattern_finishes(monkeypatch):
    ruleset = parse_rules(config(rule("miss", "bye"), rule()))
    times = iter([10.0, 10.001, 11.0])
    monkeypatch.setattr(auto_reply, "time", SimpleNamespace(monotonic=lambda: next(times)))
    assert auto_reply.first_match(ruleset, "100", "你好") is None


async def test_tracking_capacity_does_not_evict_live_dedup_or_cooldowns(tmp_path, monkeypatch):
    monkeypatch.setattr(auto_reply, "MAX_TRACKED", 2)
    now = [100.0]
    engine = await service(tmp_path, config(rule()), clock=lambda: now[0])
    assert await send(engine) is not None
    assert await send(engine, message="2") is not None
    assert await send(engine, message="3") is None
    assert await send(engine) is None
    now[0] += auto_reply.DEDUP_SECONDS
    assert await send(engine, message="3") is not None

    engine = await service(tmp_path, config(rule(), cooldown=86400), clock=lambda: now[0])
    assert await send(engine) is not None
    assert await send(engine, group="200") is not None
    now[0] += auto_reply.DEDUP_SECONDS
    assert await send(engine, bot="998") is None  # both cooldown slots are still occupied
    assert await send(engine) is None
    now[0] += 86400
    assert await send(engine, bot="998") is not None


async def test_matching_workers_are_bounded_without_queuing(tmp_path, monkeypatch):
    engine = await service(tmp_path, config(rule()))
    started = [asyncio.Event(), asyncio.Event()]
    release = threading.Event()
    lock = threading.Lock()
    loop = asyncio.get_running_loop()
    count = 0
    original = auto_reply.first_match

    def delayed(*args):
        nonlocal count
        with lock:
            index = count
            count += 1
        loop.call_soon_threadsafe(started[index].set)
        assert release.wait(2)
        return original(*args)

    monkeypatch.setattr(auto_reply, "first_match", delayed)
    pending = [asyncio.create_task(send(engine, message=str(i))) for i in range(2)]
    try:
        await asyncio.wait_for(asyncio.gather(*(event.wait() for event in started)), 1)
        assert await send(engine, message="3") is None
    finally:
        release.set()
    assert all(await asyncio.gather(*pending))
    assert count == 2


def test_cli_init_check_and_no_overwrite_without_catalog(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("ARK_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr("sys.argv", ["ark-gacha", "replies", "init"])
    main()
    path = tmp_path / "data" / "auto-replies.toml"
    body = path.read_bytes()
    assert "默认关闭" in capsys.readouterr().out
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert path.read_bytes() == body
    monkeypatch.setattr("sys.argv", ["ark-gacha", "replies", "check"])
    main()
    result = capsys.readouterr().out
    assert "规则校验通过" in result
    assert "2 条回复待填写" in result
    path.write_bytes(config(rule(pattern="[")))
    with pytest.raises(SystemExit) as exc:
        main()
    assert exc.value.code == 1
    assert "hello" in capsys.readouterr().err
    custom = tmp_path / "custom.toml"
    custom.write_bytes(config())
    monkeypatch.setattr("sys.argv", ["ark-gacha", "replies", "check", "--file", str(custom)])
    main()
    assert "0 条" in capsys.readouterr().out
