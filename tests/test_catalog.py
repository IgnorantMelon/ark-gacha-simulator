import copy
import json
import random
from datetime import UTC, datetime
from pathlib import Path

import pytest

from ark_gacha.catalog import Catalog, is_named_pool, normalize_pool, validate_pool
from ark_gacha.engine import simulate
from ark_gacha.models import CatalogSnapshot, DrawState, UserError

FIXTURES = json.loads((Path(__file__).parent / "fixtures" / "historical.json").read_bytes())


@pytest.mark.parametrize(
    "name",
    [
        "适合多种场合的强力干员",
        " 适合多种场合的强力干员\n",
        "<@ga.title>适合多种场合的强力干员</>",
        "适合 多种 场合 的 强力干员",
        "",
        "　\t",
    ],
)
def test_unnamed_and_generic_rotation_titles_are_excluded(name):
    assert not is_named_pool(name)


@pytest.mark.parametrize("name", ["海渊巡游", "前路回响", "进攻·防守·战术交汇", "地生五金"])
def test_event_titles_are_kept(name):
    assert is_named_pool(name)


@pytest.mark.parametrize("case", FIXTURES, ids=lambda c: c["client"]["gachaPoolId"])
def test_historical_raw_data_normalizes_and_can_simulate(case):
    pool = normalize_pool(case["client"], case["detail"])
    draws, _ = simulate(pool, DrawState(), 300, random.Random(314))
    assert all(draw.char_id in pool.roster[draw.rarity] for draw in draws)
    assert any(draw.rarity >= 5 for draw in draws[:10])
    if pool.rule_type == "LINKAGE":
        assert pool.rules.target_six in [draw.char_id for draw in draws[:120]]


def test_historical_rule_versions_and_rosters_are_not_retrofitted():
    pools = {c["client"]["gachaPoolId"]: normalize_pool(c["client"], c["detail"]) for c in FIXTURES}
    assert "char_103_angel" in pools["NORM_0_1_1"].roster[6]
    assert "char_4231_clemnt" not in pools["NORM_0_1_1"].roster[6]
    assert "char_103_angel" not in pools["SINGLE_78_0_1"].roster[6]
    assert pools["NORM_0_1_1"].rules.directed_threshold is None
    assert pools["SINGLE_78_0_1"].rules.directed_threshold == 150
    assert pools["CLASSIC_58_0_4"].rules.selection_thresholds == []
    assert pools["DOUBLE_78_0_2"].rules.selection_thresholds == [150, 300]
    assert pools["LINKAGE_17_0_1"].rules.target_six_mode == "once"
    assert pools["LINKAGE_74_0_1"].rules.target_six_mode == "cycle"
    assert pools["LINKAGE_77_0_1"].rules.target_five_threshold == 50
    assert pools["LINKAGE_77_0_1"].rules.complementary_five


def test_missing_roster_and_unknown_rules_rejected():
    case = copy.deepcopy(FIXTURES[0])
    case["detail"]["gachaPoolDetail"]["detailInfo"]["availCharInfo"]["perAvailList"] = []
    with pytest.raises(ValueError, match="星级分布"):
        normalize_pool(case["client"], case["detail"])
    case = copy.deepcopy(FIXTURES[0])
    case["client"]["guarantee5Count"] = 20
    with pytest.raises(ValueError, match="未知首次"):
        normalize_pool(case["client"], case["detail"])


def test_probability_and_up_validation(pool):
    with pytest.raises(ValueError, match="基础概率"):
        validate_pool(pool.model_copy(update={"rates": {6: 0.2, 5: 0.08, 4: 0.5, 3: 0.4}}))
    bad = pool.model_copy(deep=True)
    bad.up[0].characters = ["unknown"]
    with pytest.raises(ValueError, match="UP名单"):
        validate_pool(bad)


def publish_payloads():
    case = FIXTURES[0]
    clients, details = [], []
    for i in range(100):
        client = copy.deepcopy(case["client"])
        client["gachaPoolId"] = f"NORM_FIXTURE_{i}"
        client["gachaPoolName"] = "测试专属寻访"
        clients.append(client)
        detail = copy.deepcopy(case["detail"])
        detail["gachaPoolId"] = client["gachaPoolId"]
        details.append(detail)
    roster = normalize_pool(case["client"], case["detail"]).roster
    operators = {c: {"name": c} for chars in roster.values() for c in chars}
    operators.update({f"dummy{i}": {"name": f"Dummy{i}"} for i in range(100)})
    payloads = {
        "client": {"gachaPoolClient": clients},
        "details": {"gachaPoolClient": details},
        "operators": operators,
    }
    bodies = {key: json.dumps(value).encode() for key, value in payloads.items()}
    urls = {key: "https://example.invalid/fixture" for key in bodies}
    return payloads, bodies, urls


def test_publish_failure_preserves_pointer_and_active_version_is_loadable(tmp_path):
    catalog = Catalog(tmp_path)
    payloads, bodies, urls = publish_payloads()
    report = catalog.publish(payloads, bodies, urls)
    version = report["version"]
    snapshot = catalog.load()
    assert len(snapshot.pools) == 100
    payloads["details"]["gachaPoolClient"] = []
    with pytest.raises(UserError, match="不完整"):
        catalog.publish(payloads, bodies, urls)
    assert catalog.load().version == version
    assert catalog.load(version) == snapshot


def test_normalized_rules_participate_in_content_version(tmp_path):
    catalog = Catalog(tmp_path)
    payloads, bodies, urls = publish_payloads()
    first = catalog.publish(payloads, bodies, urls)
    payloads["client"]["gachaPoolClient"][0]["guarantee5Avail"] = 0
    second = catalog.publish(payloads, bodies, urls)
    assert first["version"] != second["version"]
    assert not catalog.load().pools[0].rules.first_ten
    assert catalog.load(first["version"]).pools[0].rules.first_ten


def test_publish_does_not_collect_generic_rotation_pools(tmp_path):
    catalog = Catalog(tmp_path)
    payloads, bodies, urls = publish_payloads()
    payloads["client"]["gachaPoolClient"][0]["gachaPoolName"] = "适合多种场合的强力干员"
    report = catalog.publish(payloads, bodies, urls)
    assert report["supported"] == 99
    assert report["excluded"] == [
        {
            "id": "NORM_FIXTURE_0",
            "name": "适合多种场合的强力干员",
            "reason": "范围外：常规轮换池或无专属卡池名",
        }
    ]
    assert all(is_named_pool(pool.name) for pool in catalog.load().pools)


def test_old_snapshot_filters_rotation_but_retains_named_normal_and_reruns(pool):
    pools = [
        pool.model_copy(update={"id": "ROTATION", "name": "适合多种场合的强力干员"}),
        pool.model_copy(update={"id": "EVENT_ORIGINAL", "name": "地生五金"}),
        pool.model_copy(update={"id": "EVENT_RERUN", "name": "地生五金"}),
    ]
    snapshot = CatalogSnapshot(
        version="0123456789abcdef",
        fetched_at="fixture",
        sources={},
        operators={},
        pools=pools,
        excluded=[],
    )
    assert [p.id for p in Catalog.available(snapshot, datetime(2026, 10, 26, tzinfo=UTC))] == [
        "EVENT_ORIGINAL",
        "EVENT_RERUN",
    ]


def test_future_filter_and_stable_sort(pool):
    pools = [
        pool.model_copy(update={"id": "Z"}),
        pool.model_copy(update={"id": "A"}),
        pool.model_copy(update={"id": "FUTURE", "opened_at": 9999999999}),
    ]
    snapshot = CatalogSnapshot(
        version="0123456789abcdef",
        fetched_at="fixture",
        sources={},
        operators={},
        pools=pools,
        excluded=[],
    )
    assert [p.id for p in Catalog.available(snapshot, datetime(2026, 10, 26, tzinfo=UTC))] == [
        "A",
        "Z",
    ]
