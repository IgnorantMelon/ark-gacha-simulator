import argparse
import asyncio
import json
import random
from datetime import UTC, datetime
from pathlib import Path

from .catalog import Catalog, validate_pool
from .config import Settings
from .engine import simulate
from .models import DrawMode, DrawState, UserError
from .render import Renderer
from .storage import Store


async def execute(args) -> None:
    settings = Settings()
    catalog = Catalog(settings.data_dir / "catalog")
    if args.command == "sync":
        report = await catalog.sync()
        print(
            f"版本 {report['version']}：支持 {report['supported']} 池，"
            f"排除/失败 {len(report['excluded'])} 池"
        )
        print(settings.data_dir / "catalog" / "versions" / f"{report['version']}.report.json")
        return
    snapshot = catalog.load()
    available = catalog.available(snapshot, datetime.now(UTC))
    if args.command == "validate":
        for pool in snapshot.pools:
            validate_pool(pool)
            draws, _ = simulate(pool, DrawState(), 300, random.Random(20261026))
            if any(r.char_id not in pool.roster[r.rarity] for r in draws):
                raise UserError(f"模拟结果不属于候选名单：{pool.id}")
        characters = sorted({c for p in snapshot.pools for cs in p.roster.values() for c in cs})
        if args.assets:
            renderer = Renderer(settings.data_dir / "avatars", settings.font_path)
            await renderer.ensure(characters)
        audit = {
            "version": snapshot.version,
            "checked_at": datetime.now(UTC).isoformat(),
            "validated_pools": len(snapshot.pools),
            "opened_pools": len(available),
            "draws_per_pool": 300,
            "total_draws": len(snapshot.pools) * 300,
            "unique_avatars": len(characters),
            "avatars_verified": args.assets,
        }
        path = catalog.root / "versions" / f"{snapshot.version}.audit.json"
        await asyncio.to_thread(
            path.write_text, json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(
            f"校验通过：{len(snapshot.pools)} 池，其中已开放 {len(available)} 池；"
            f"已模拟 {len(snapshot.pools) * 300} 抽。"
        )
        if args.assets:
            print(f"头像校验通过：{len(characters)} 张。")
        print(path.resolve())
        return
    if args.command == "list":
        for pool in available[: args.limit]:
            print(
                f"{pool.id} | {pool.name} | "
                + " / ".join(snapshot.operators[c] for c in pool.direct_up(6))
            )
        return
    pool = next((p for p in available if p.id == args.pool), None)
    if pool is None:
        raise UserError("卡池不存在、尚未开放或未通过校验。")
    renderer = Renderer(settings.data_dir / "avatars", settings.font_path)
    if args.command == "select":
        await renderer.prepare(pool)
        store = Store(settings.database)
        await store.open()
        try:
            await store.set_active(pool.id, snapshot.version)
        finally:
            await store.close()
        print(f"已切换：{pool.id} | {pool.name}")
        return
    if args.command == "demo":
        mode = DrawMode(args.mode)
        results, _ = simulate(pool, DrawState(), 10 if mode == DrawMode.TEN else 300)
        images = await renderer.render(pool, snapshot.operators, results, mode, 0)
        destination = Path(args.output)
        await asyncio.to_thread(destination.mkdir, parents=True, exist_ok=True)
        for i, image in enumerate(images, 1):
            path = destination / f"{mode.value}-{i}.png"
            await asyncio.to_thread(path.write_bytes, image)
            print(path.resolve())
        if args.details:
            print(json.dumps([r.model_dump() for r in results], ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="明日方舟 QQ 抽卡机器人")
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ["run", "sync"]:
        commands.add_parser(name)
    validation = commands.add_parser("validate")
    validation.add_argument("--assets", action="store_true", help="下载并检查全部历史池头像")
    listing = commands.add_parser("list")
    listing.add_argument("--limit", type=int, default=10)
    selection = commands.add_parser("select")
    selection.add_argument("pool")
    demo = commands.add_parser("demo")
    demo.add_argument("pool")
    demo.add_argument("--mode", choices=["ten", "well"], default="ten")
    demo.add_argument("--output", default="output")
    demo.add_argument("--details", action="store_true", help="仅离线调试，输出模拟名单")
    args = parser.parse_args()
    try:
        if args.command == "run":
            from .bot import run_bot

            run_bot()
        else:
            asyncio.run(execute(args))
    except (UserError, OSError) as exc:
        parser.exit(1, f"错误：{exc}\n")


if __name__ == "__main__":
    main()
