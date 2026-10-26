import asyncio
import hashlib
import json
import math
import os
import re
import unicodedata
import uuid
from datetime import UTC, datetime
from pathlib import Path

import httpx

from .models import CatalogSnapshot, PoolDefinition, RuleProfile, UpGroup, UserError

CLIENT_URL = (
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/"
    "master/zh_CN/gamedata/excel/gacha_table.json"
)
CHARACTER_URL = (
    "https://raw.githubusercontent.com/Kengxxiao/ArknightsGameData/"
    "master/zh_CN/gamedata/excel/character_table.json"
)
DETAIL_URL = "https://weedy.prts.wiki/gacha_table.json"
SUPPORTED = {"NORMAL", "SINGLE", "DOUBLE", "CLASSIC", "CLASSIC_DOUBLE", "LIMITED", "LINKAGE"}
GENERIC_POOL_NAMES = {"适合多种场合的强力干员"}


def is_named_pool(name: str) -> bool:
    """Match the displayed title, including legacy snapshots with rich-text tags."""
    display_name = unicodedata.normalize("NFKC", re.sub(r"<[^>]*>", "", name))
    display_name = re.sub(r"\s+", "", display_name)
    return bool(display_name) and display_name not in GENERIC_POOL_NAMES


def normalize_pool(client: dict, detail: dict) -> PoolDefinition:
    if client.get("guarantee5Avail") and client.get("guarantee5Count") != 10:
        raise ValueError("未知首次五星保底阈值")
    info = detail["gachaPoolDetail"]["detailInfo"]
    roster, rates = {}, {}
    for group in info["availCharInfo"]["perAvailList"]:
        rarity = group["rarityRank"] + 1
        roster[rarity] = group["charIdList"]
        rates[rarity] = group["totalPercent"]
    up = [
        UpGroup(rarity=g["rarityRank"] + 1, characters=g["charIdList"], per_character=g["percent"])
        for g in (info.get("upCharInfo") or {}).get("perCharList", [])
    ]
    text = re.sub(
        r"<[^>]*>",
        "",
        "\n".join(
            obj.get("param") or ""
            for obj in info.get("gachaObjList", [])
            if obj.get("gachaObject") == "TEXT"
        ),
    )
    rule_type = client["gachaRuleType"]
    direct6 = list(dict.fromkeys(c for group in up if group.rarity == 6 for c in group.characters))
    direct5 = list(dict.fromkeys(c for group in up if group.rarity == 5 for c in group.characters))
    rules = RuleProfile(
        first_ten=bool(client.get("guarantee5Avail")),
        extra_gift_at_300=bool((client.get("limitParam") or {}).get("hasFreeChar")),
    )
    if "定向选调" in text:
        if len(direct6) != 1 or not re.search(r"连续\s*150\s*次", text):
            raise ValueError("无法识别定向选调规则")
        rules.directed_threshold = 150
        rules.directed_target = direct6[0]
    if "标准选调" in text or "中坚选调" in text:
        thresholds = [int(n) for n in re.findall(r"累计寻访\s*(\d+)\s*次后", text)]
        if thresholds != [150, 300] or len(direct6) != 2:
            raise ValueError("无法识别双UP选调规则")
        rules.selection_thresholds = thresholds
    if rule_type == "LINKAGE":
        params = client.get("linkageParam") or {}
        if len(direct6) != 1:
            raise ValueError("联动六星目标不唯一")
        rules.target_six = direct6[0]
        if "loopTarget6Count" in params and "每120次" in text.replace(" ", ""):
            rules.target_six_threshold = int(params["loopTarget6Count"])
            rules.target_six_mode = "cycle"
        elif "guaranteeTarget6Count" in params and "前120次" in text.replace(" ", ""):
            rules.target_six_threshold = int(params["guaranteeTarget6Count"])
            rules.target_six_mode = "once"
        else:
            raise ValueError("无法识别联动六星保底")
        rules.complementary_five = "另一名当期概率上升的5星干员" in text
        if rules.complementary_five and len(direct5) != 2:
            raise ValueError("无法识别联动五星防重复目标")
        if "如果前50次寻访没有获得本期出率上升的5星干员" in text:
            rules.target_five_threshold = 50
    if not all(s in text for s in ["50", "2%", "100%"]):
        raise ValueError("六星递增保底缺少规则依据")
    domain = (
        "standard"
        if rule_type in {"NORMAL", "SINGLE", "DOUBLE"}
        else "kernel"
        if rule_type in {"CLASSIC", "CLASSIC_DOUBLE"}
        else client["gachaPoolId"]
    )
    pool = PoolDefinition(
        id=client["gachaPoolId"],
        name=client["gachaPoolName"],
        opened_at=client["openTime"],
        closed_at=client["endTime"],
        rule_type=rule_type,
        pity_domain=domain,
        rates=rates,
        roster=roster,
        up=up,
        weight_up={g["charId"]: g["weight"] for g in info.get("weightUpCharInfoList") or []},
        rules=rules,
        evidence=text,
    )
    validate_pool(pool)
    return pool


def validate_pool(pool: PoolDefinition) -> None:
    if set(pool.rates) != {3, 4, 5, 6} or set(pool.roster) != {3, 4, 5, 6}:
        raise ValueError("星级分布不完整")
    if not math.isclose(sum(pool.rates.values()), 1.0, abs_tol=1e-5):
        raise ValueError("基础概率之和不为1")
    if not math.isclose(pool.rates[6], 0.02, abs_tol=1e-6):
        raise ValueError("未知六星基础概率")
    seen = set()
    for rarity, chars in pool.roster.items():
        if not chars or len(chars) != len(set(chars)) or seen.intersection(chars):
            raise ValueError("名单为空、重复或跨星级冲突")
        seen.update(chars)
        if not math.isfinite(pool.rates[rarity]) or pool.rates[rarity] <= 0:
            raise ValueError("星级概率无效")
        groups = [g for g in pool.up if g.rarity == rarity]
        up_chars = [c for g in groups for c in g.characters]
        if len(up_chars) != len(set(up_chars)) or not set(up_chars) <= set(chars):
            raise ValueError("UP名单不属于候选名单或有重复")
        if any(not math.isfinite(g.per_character) or g.per_character <= 0 for g in groups):
            raise ValueError("UP概率无效")
        mass = sum(g.per_character * len(g.characters) for g in groups)
        if mass > 1.00001 or (set(up_chars) == set(chars) and abs(mass - 1) > 1e-4):
            raise ValueError("UP概率分配无效")
    if any(c not in seen or not math.isfinite(w) or w <= 0 for c, w in pool.weight_up.items()):
        raise ValueError("加权干员或权重无效")


class Catalog:
    def __init__(self, root: Path):
        self.root = root
        self._snapshots: dict[str, CatalogSnapshot] = {}
        self.sync_lock = asyncio.Lock()

    def load(self, version: str | None = None) -> CatalogSnapshot:
        if version is None:
            try:
                version = (self.root / "current").read_text(encoding="utf-8").strip()
            except FileNotFoundError as exc:
                raise UserError("尚无卡池数据，请管理员先执行更新卡池。") from exc
        if not re.fullmatch(r"[0-9a-f]{16}", version):
            raise UserError("卡池版本无效。")
        if version not in self._snapshots:
            try:
                snapshot = CatalogSnapshot.model_validate_json(
                    (self.root / "versions" / f"{version}.json").read_bytes()
                )
            except (OSError, ValueError) as exc:
                raise UserError("卡池版本损坏，请更新数据。") from exc
            if snapshot.version != version or snapshot.schema_version != 1:
                raise UserError("卡池版本不兼容。")
            self._snapshots[version] = snapshot
        return self._snapshots[version]

    @staticmethod
    def available(snapshot: CatalogSnapshot, now: datetime) -> list[PoolDefinition]:
        return sorted(
            (p for p in snapshot.pools if p.opened_at <= now.timestamp() and is_named_pool(p.name)),
            key=lambda p: (-p.opened_at, p.id),
        )

    async def sync(self) -> dict:
        async with self.sync_lock:
            urls = {"client": CLIENT_URL, "details": DETAIL_URL, "operators": CHARACTER_URL}
            async with httpx.AsyncClient(timeout=60, follow_redirects=True) as client:

                async def fetch(key, url):
                    response = await client.get(url)
                    response.raise_for_status()
                    return key, response.content

                bodies = dict(await asyncio.gather(*(fetch(k, v) for k, v in urls.items())))
            payloads = {k: json.loads(v) for k, v in bodies.items()}
            return await asyncio.to_thread(self.publish, payloads, bodies, urls)

    def publish(self, payloads: dict, bodies: dict, urls: dict) -> dict:
        details = {p["gachaPoolId"]: p for p in payloads["details"]["gachaPoolClient"]}
        characters = payloads["operators"]
        pools, excluded = [], []
        entries = payloads["client"]["gachaPoolClient"]
        if len(entries) < 100 or len(details) < 100 or len(characters) < 100:
            raise UserError("数据源返回不完整，未发布新版本。")
        if len({entry["gachaPoolId"] for entry in entries}) != len(entries):
            raise UserError("卡池ID重复，未发布新版本。")
        for entry in entries:
            pool_id = entry["gachaPoolId"]
            try:
                if not is_named_pool(entry.get("gachaPoolName") or ""):
                    raise ValueError("范围外：常规轮换池或无专属卡池名")
                if entry["gachaRuleType"] not in SUPPORTED:
                    raise ValueError("范围外：自选UP或未持有保底等特殊池")
                pool = normalize_pool(entry, details[pool_id])
                if any(c not in characters for group in pool.roster.values() for c in group):
                    raise ValueError("干员字典缺少历史名单成员")
                pools.append(pool)
            except (KeyError, TypeError, ValueError) as exc:
                reason = str(exc) if isinstance(exc, ValueError) else "详情字段缺失或不兼容"
                excluded.append(
                    {"id": pool_id, "name": entry.get("gachaPoolName", ""), "reason": reason}
                )
        for entry in payloads["client"].get("newbeeGachaPoolClient", []):
            excluded.append(
                {"id": entry["gachaPoolId"], "name": "新手卡池", "reason": "范围外：新手池"}
            )
        if not pools:
            raise UserError("没有通过校验的卡池，未发布新版本。")
        hashes = {k: hashlib.sha256(v).hexdigest() for k, v in bodies.items()}
        operators = {key: value["name"] for key, value in characters.items()}
        fingerprint = {
            "schema": 1,
            "sources": hashes,
            "operators": operators,
            "pools": [p.model_dump() for p in pools],
            "excluded": excluded,
        }
        version = hashlib.sha256(json.dumps(fingerprint, sort_keys=True).encode()).hexdigest()[:16]
        snapshot = CatalogSnapshot(
            version=version,
            fetched_at=datetime.now(UTC).isoformat(),
            sources={k: {"url": urls[k], "sha256": h} for k, h in hashes.items()},
            operators=operators,
            pools=pools,
            excluded=excluded,
        )
        report = {
            "version": version,
            "fetched_at": snapshot.fetched_at,
            "catalog_entries": len(entries),
            "supported": len(pools),
            "excluded": excluded,
            "sources": snapshot.sources,
            "pools": [
                {
                    "id": p.id,
                    "name": p.name,
                    "rule_type": p.rule_type,
                    "roster_counts": {r: len(cs) for r, cs in p.roster.items()},
                    "rules": p.rules.model_dump(),
                }
                for p in pools
            ],
        }
        self.root.mkdir(parents=True, exist_ok=True)
        (self.root / "versions").mkdir(exist_ok=True)
        (self.root / "sources" / version).mkdir(parents=True, exist_ok=True)
        for key, body in bodies.items():
            (self.root / "sources" / version / f"{key}.json").write_bytes(body)
        target = self.root / "versions" / f"{version}.json"
        if not target.exists():
            temp = target.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temp.write_text(snapshot.model_dump_json(), encoding="utf-8")
            os.replace(temp, target)
        report_path = self.root / "versions" / f"{version}.report.json"
        report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        pointer = self.root / f"current.{uuid.uuid4().hex}.tmp"
        pointer.write_text(version, encoding="utf-8")
        os.replace(pointer, self.root / "current")
        return report
