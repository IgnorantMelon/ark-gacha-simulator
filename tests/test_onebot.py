"""Real NoneBot process + reverse WebSocket + actual PNG generation; no QQ login."""

import asyncio
import base64
import io
import json
import os
import socket
import sys
import time

import pytest
import websockets
from PIL import Image
from websockets.exceptions import InvalidStatus


async def test_real_reverse_websocket_text_images_and_failed_delivery_history(app):
    service, _ = app
    root = service.settings.data_dir
    snapshot = service.catalog.load()
    versions = root / "catalog" / "versions"
    versions.mkdir()
    (versions / f"{snapshot.version}.json").write_text(snapshot.model_dump_json(), encoding="utf-8")
    avatars = root / "avatars"
    avatars.mkdir()
    for char in snapshot.operators:
        Image.new("RGB", (128, 128), "#445566").save(avatars / f"{char}.png")
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = {
        **os.environ,
        "PYTHONUTF8": "1",
        "ARK_DATA_DIR": str(root),
        "ARK_PORT": str(port),
        "ARK_HOST": "127.0.0.1",
        "ARK_ONEBOT_TOKEN": "test-token-local-only",
        "ARK_ALLOWED_GROUPS": '["100"]',
        "ARK_ADMIN_QQ": '["123"]',
    }
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "ark_gacha.cli",
        "run",
        env=env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    headers = {"X-Self-ID": "999", "Authorization": "Bearer test-token-local-only"}
    url = f"ws://127.0.0.1:{port}/onebot/v11/ws"
    try:
        ws = None
        for _ in range(100):
            if process.returncode is not None:
                raise AssertionError((await process.stdout.read()).decode())
            try:
                ws = await websockets.connect(url, additional_headers=headers, open_timeout=1)
                break
            except OSError:
                await asyncio.sleep(0.1)
        assert ws is not None, "NoneBot failed to start"
        async with ws:
            with pytest.raises(InvalidStatus):
                await websockets.connect(url, additional_headers={"X-Self-ID": "998"})
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
            message_id = 0

            async def command(text, *, fail_delivery=False):
                nonlocal message_id
                message_id += 1
                await ws.send(
                    json.dumps(
                        {
                            "time": int(time.time()),
                            "self_id": 999,
                            "post_type": "message",
                            "message_type": "group",
                            "sub_type": "normal",
                            "message_id": message_id,
                            "group_id": 100,
                            "user_id": 123,
                            "anonymous": None,
                            "message": [{"type": "text", "data": {"text": text}}],
                            "raw_message": text,
                            "font": 0,
                            "sender": {"user_id": 123, "nickname": "Test", "role": "member"},
                        }
                    )
                )
                while True:
                    action = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                    is_send = action["action"] in {"send_group_msg", "send_msg"}
                    await ws.send(
                        json.dumps(
                            {
                                "status": "failed" if fail_delivery and is_send else "ok",
                                "retcode": 100 if fail_delivery and is_send else 0,
                                "data": {"message_id": message_id}
                                if is_send
                                else {"user_id": 999, "nickname": "MockQQ"},
                                "echo": action["echo"],
                            }
                        )
                    )
                    if is_send:
                        return action["params"]["message"]

            current = await command("方舟卡池")
            assert current == [{"type": "text", "data": {"text": "测试卡池 | char_up"}}]
            listing = await command("卡池列表 1")
            assert listing[0]["data"]["text"] == "测试卡池 | char_up | NORMAL_TEST"
            help_reply = await command("help")
            help_text = help_reply[0]["data"]["text"]
            assert "切换卡池 latest" in help_text.splitlines()
            for alias in ["/help", "帮助", "/帮助"]:
                assert await command(alias) == help_reply
            for pool_id in ["normal_test", "LATEST"]:
                switched = await command(f"切换卡池 {pool_id}")
                assert switched[0]["data"]["text"] == "已切换卡池：NORMAL_TEST | 测试卡池"
            pity = await command("查看水位")
            assert pity[0]["data"]["text"] == "连续未出六星：0抽\n本池累计抽数：0抽"
            ten = await command("方舟十连", fail_delivery=True)
            assert ten[0]["type"] == "image"
            payload = ten[0]["data"]["file"]
            assert payload.startswith("base64://")
            with Image.open(io.BytesIO(base64.b64decode(payload[9:]))) as image:
                assert image.format == "PNG" and image.size == (898, 554)
            history = await command("查看历史")
            assert history == ten  # exact bytes despite the earlier OneBot send failure
            well = await command("方舟一井")
            assert well[0]["type"] == "image"
            with Image.open(io.BytesIO(base64.b64decode(well[0]["data"]["file"][9:]))) as image:
                assert image.format == "PNG" and image.width == 1068
            assert await command("查看历史") == ten
    finally:
        if process.returncode is None:
            process.terminate()
        output, _ = await asyncio.wait_for(process.communicate(), timeout=10)
        if output:
            print(output.decode("utf-8", errors="replace"))
