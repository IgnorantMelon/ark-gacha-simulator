"""Exercise real NoneBot routing and sends over reverse WebSocket, without QQ login."""

import asyncio
import json
import os
import socket
import sys
import time
from contextlib import suppress
from importlib.resources import files

import pytest
import pytest_asyncio
import websockets
from test_auto_reply import config, rule


class OneBotClient:
    def __init__(self, ws):
        self.ws = ws
        self.sent = asyncio.Queue()
        self.sequence = 0
        self.fail_delivery = False

    async def receive(self):
        async for raw in self.ws:
            action = json.loads(raw)
            is_send = action["action"] in {"send_group_msg", "send_msg"}
            failed = is_send and self.fail_delivery
            data = (
                {"message_id": self.sequence} if is_send else {"user_id": 999, "nickname": "MockQQ"}
            )
            if action["action"] == "get_msg":
                data = {
                    "time": int(time.time()),
                    "message_type": "group",
                    "message_id": 777,
                    "real_id": 777,
                    "sender": {"user_id": 999, "nickname": "MockQQ"},
                    "message": [{"type": "text", "data": {"text": "机器人之前的回复"}}],
                }
            await self.ws.send(
                json.dumps(
                    {
                        "status": "failed" if failed else "ok",
                        "retcode": 100 if failed else 0,
                        "data": data,
                        "echo": action["echo"],
                    }
                )
            )
            if is_send:
                self.sent.put_nowait(action["params"])

    async def post(
        self, text, *, user=123, group=100, message_id=None, private=False, segments=None
    ):
        self.sequence += 1
        event = {
            "time": int(time.time()),
            "self_id": 999,
            "post_type": "message",
            "message_type": "private" if private else "group",
            "sub_type": "friend" if private else "normal",
            "message_id": message_id if message_id is not None else self.sequence,
            "user_id": user,
            "message": segments
            if segments is not None
            else [{"type": "text", "data": {"text": text}}],
            "raw_message": text,
            "font": 0,
            "sender": {"user_id": user, "nickname": "Test", "role": "member"},
        }
        if not private:
            event.update(group_id=group, anonymous=None)
        await self.ws.send(json.dumps(event))

    async def take(self):
        return await asyncio.wait_for(self.sent.get(), 3)

    async def quiet(self):
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(self.sent.get(), 0.2)


@pytest_asyncio.fixture
async def connection(tmp_path):
    path = tmp_path / "auto-replies.toml"
    path.write_bytes(
        config(
            rule("command", "help", "不应抢占命令"),
            rule("morning", "早安！?", "早上好", "mode = 'fullmatch'\nat_sender = true"),
            rule("scoped", "限定", "限定回复", "groups = ['200']"),
            rule("cooldown", "冷却", "只回一次", "cooldown_seconds = 30"),
            rule("fallback", "冷却", "不应落入后续规则"),
            rule("literal", "文字", "[CQ:at,qq=all]"),
            rule("failed", "发送失败", "失败回复", "cooldown_seconds = 30"),
            "id = 'multi'\npattern = '多行'\nreply = '''第一行\n第二行'''",
        )
    )
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ark_gacha.cli",
        "run",
        env={
            **os.environ,
            "PYTHONUTF8": "1",
            "ARK_DATA_DIR": str(tmp_path),
            "ARK_HOST": "127.0.0.1",
            "ARK_PORT": str(port),
            "ARK_ONEBOT_TOKEN": "auto-reply-test-token",
            "ARK_ALLOWED_GROUPS": '["100", "200"]',
            "ARK_ADMIN_QQ": "[]",
            "NICKNAME": '["小舟"]',
        },
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    receiver = None
    ws = None
    try:
        for _ in range(100):
            if process.returncode is not None:
                pytest.fail((await process.stdout.read()).decode("utf-8", errors="replace"))
            try:
                ws = await websockets.connect(
                    f"ws://127.0.0.1:{port}/onebot/v11/ws",
                    additional_headers={
                        "X-Self-ID": "999",
                        "Authorization": "Bearer auto-reply-test-token",
                    },
                    open_timeout=1,
                )
                break
            except OSError:
                await asyncio.sleep(0.1)
        assert ws is not None, "NoneBot failed to start"
        client = OneBotClient(ws)
        receiver = asyncio.create_task(client.receive())
        await ws.send(
            json.dumps(
                {
                    "time": int(time.time()),
                    "self_id": 999,
                    "post_type": "meta_event",
                    "meta_event_type": "lifecycle",
                    "sub_type": "connect",
                }
            )
        )
        yield client, path
    finally:
        if receiver:
            receiver.cancel()
            with suppress(asyncio.CancelledError):
                await receiver
        if ws:
            await ws.close()
        if process.returncode is None:
            process.terminate()
        output, _ = await asyncio.wait_for(process.communicate(), 10)
        if output:
            print(output.decode("utf-8", errors="replace"))


def text_of(params):
    return "".join(
        segment["data"]["text"] for segment in params["message"] if segment["type"] == "text"
    )


async def test_routing_mentions_plaintext_scopes_dedup_and_failure(connection):
    client, _ = connection
    await client.post("help")
    assert "方舟十连" in text_of(await client.take())
    await client.quiet()  # auto-reply rule also matches help, but must not send

    await client.post("早安！", message_id=10001)
    params = await client.take()
    assert params["group_id"] == 100
    assert params["message"][0] == {"type": "at", "data": {"qq": "123"}}
    assert text_of(params).strip() == "早上好"
    await client.post("早安！", message_id=10001)
    await client.quiet()
    await client.post("早安！", group=200, message_id=10001)
    assert (await client.take())["group_id"] == 200

    await client.post("早安", group=300)
    await client.post("早安", user=999)
    await client.post("早安", private=True)
    await client.post("大家早安")
    await client.post("限定")
    await client.post("", segments=[{"type": "image", "data": {"file": "早安.png"}}])
    await client.quiet()
    await client.post("限定", group=200)
    assert text_of(await client.take()) == "限定回复"

    await client.post(
        "早安",
        segments=[
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": " 早安 "}},
        ],
    )
    assert text_of(await client.take()).strip() == "早上好"
    await client.post("多行")
    assert text_of(await client.take()) == "第一行\n第二行"
    await client.post("文字")
    assert (await client.take())["message"] == [
        {"type": "text", "data": {"text": "[CQ:at,qq=all]"}}
    ]

    await client.post("冷却")
    assert text_of(await client.take()) == "只回一次"
    await client.post("冷却", user=456)
    await client.quiet()
    await client.post("冷却", group=200)
    assert text_of(await client.take()) == "只回一次"
    client.fail_delivery = True
    await client.post("发送失败", message_id=20001)
    assert text_of(await client.take()) == "失败回复"
    client.fail_delivery = False
    await client.post("发送失败", message_id=20001)
    await client.post("发送失败", message_id=20002)
    await client.quiet()
    await client.post("help")
    assert "方舟十连" in text_of(await client.take())


async def test_live_reload_invalid_config_retention_deletion_and_disable(connection):
    client, path = connection
    path.write_bytes(config(rule("new", "更新", "更新成功")))
    await asyncio.sleep(0.3)
    await client.post("更新")
    assert text_of(await client.take()) == "更新成功"
    path.write_bytes(config(rule(pattern="[")))
    await asyncio.sleep(0.3)
    await client.post("更新")
    assert text_of(await client.take()) == "更新成功"
    path.unlink()
    await asyncio.sleep(0.3)
    await client.post("更新")
    assert text_of(await client.take()) == "更新成功"
    path.write_bytes(config(rule("new", "更新", "更新成功"), enabled=False))
    await asyncio.sleep(0.3)
    await client.post("更新")
    await client.quiet()
    path.write_bytes(config(rule("new", "更新", "重新开启")))
    await asyncio.sleep(0.3)
    await client.post("更新")
    assert text_of(await client.take()) == "重新开启"


async def test_sl_and_who_match_only_with_real_self_mentions(connection):
    client, path = connection
    body = files("ark_gacha").joinpath("assets/auto-replies.example.toml").read_bytes()
    body = body.replace(b"enabled = false", b"enabled = true", 1)
    body = body.replace(b"reload_interval_seconds = 5", b"reload_interval_seconds = 0.1")
    body = body.replace(b"default_cooldown_seconds = 30", b"default_cooldown_seconds = 0")
    path.write_bytes(body)  # both empty replies are valid drafts and must replace old rules
    await asyncio.sleep(0.3)
    await client.post("#sl")
    await client.post("早安")
    await client.quiet()
    body = body.replace(b'reply = ""', b'reply = "fixture reply"')
    path.write_bytes(body)
    await asyncio.sleep(0.3)
    await client.post("#sl")
    assert text_of(await client.take()) == "fixture reply"
    for text in ["#sl extra", "prefix#sl", "#SL", "是谁", "@999 是谁", "小舟，你是谁"]:
        await client.post(text)
    for qq in ["123", "all"]:
        await client.post(
            "你是谁",
            segments=[
                {"type": "at", "data": {"qq": qq}},
                {"type": "text", "data": {"text": "你是谁"}},
            ],
        )
    await client.post(
        "你是谁",
        segments=[
            {"type": "reply", "data": {"id": "777"}},
            {"type": "text", "data": {"text": "你是谁"}},
        ],
    )
    await client.post("[CQ:at,qq=999] 你是谁")
    await client.quiet()
    segments = [
        {"type": "text", "data": {"text": "请问"}},
        {"type": "text", "data": {"text": "你是谁？"}},
    ]
    for position in [0, 1, 2]:
        mentioned = list(segments)
        mentioned.insert(position, {"type": "at", "data": {"qq": "999"}})
        await client.post("请问你是谁？", segments=mentioned)
        assert text_of(await client.take()) == "fixture reply"
    await client.post(
        "你是谁",
        segments=[
            {"type": "reply", "data": {"id": "777"}},
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "你是谁"}},
        ],
    )
    assert text_of(await client.take()) == "fixture reply"
    await client.post(
        "你好",
        segments=[
            {"type": "at", "data": {"qq": "999"}},
            {"type": "text", "data": {"text": "你好"}},
        ],
    )
    await client.quiet()
