import asyncio
import copy
import json
import random
from pathlib import Path

import httpx
import pytest

from ark_gacha.catalog import (
    CHARACTER_CDN_URL,
    CHARACTER_URL,
    CLIENT_CDN_URL,
    CLIENT_URL,
    DETAIL_URL,
    Catalog,
    is_named_pool,
    normalize_pool,
    validate_pool,
)
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


def mock_catalog_http(monkeypatch, handle):
    client_type = httpx.AsyncClient
    monkeypatch.setattr(
        "ark_gacha.catalog.httpx.AsyncClient",
        lambda **kwargs: client_type(transport=httpx.MockTransport(handle), **kwargs),
    )


def source_responses(payloads):
    return {
        CLIENT_URL: payloads["client"],
        CLIENT_CDN_URL: payloads["client"],
        CHARACTER_URL: payloads["operators"],
        CHARACTER_CDN_URL: payloads["operators"],
        DETAIL_URL: payloads["details"],
    }


async def test_sync_prefers_raw_and_records_sources_and_loads_offline(tmp_path, monkeypatch):
    payloads, _, _ = publish_payloads()
    responses = source_responses(payloads)
    requests = []

    def handle(request):
        url = str(request.url)
        requests.append(url)
        return httpx.Response(200, json=responses[url])

    mock_catalog_http(monkeypatch, handle)
    report = await Catalog(tmp_path).sync()
    assert set(requests) == {CLIENT_URL, CHARACTER_URL, DETAIL_URL}
    assert report["sources"]["client"]["url"] == CLIENT_URL
    assert report["sources"]["operators"]["url"] == CHARACTER_URL
    # A new instance (as after restart) loads only persisted files.
    snapshot = Catalog(tmp_path).load()
    assert snapshot.version == report["version"] and len(snapshot.pools) == 100
    for key, metadata in snapshot.sources.items():
        import hashlib

        body = (tmp_path / "sources" / snapshot.version / f"{key}.json").read_bytes()
        assert hashlib.sha256(body).hexdigest() == metadata["sha256"]


@pytest.mark.parametrize(
    "failure", ["timeout", "http", "html", "incomplete", "missing_operator", "missing_pool"]
)
async def test_sync_switches_both_tables_to_cdn(tmp_path, monkeypatch, failure):
    catalog = Catalog(tmp_path)
    payloads, bodies, urls = publish_payloads()
    catalog.publish(payloads, bodies, urls)
    responses = source_responses(payloads)
    requests = []

    def handle(request):
        url = str(request.url)
        requests.append(url)
        if url == CHARACTER_URL:
            if failure == "timeout":
                raise httpx.ConnectTimeout("fixture", request=request)
            if failure == "http":
                return httpx.Response(503)
            if failure == "html":
                return httpx.Response(200, text="<html>unavailable</html>")
            operators = copy.deepcopy(payloads["operators"])
            if failure == "incomplete":
                operators = {}
            if failure == "missing_operator":
                roster = normalize_pool(FIXTURES[0]["client"], FIXTURES[0]["detail"]).roster
                del operators[roster[6][0]]
            return httpx.Response(200, json=operators)
        if url == CLIENT_URL and failure == "missing_pool":
            table = copy.deepcopy(payloads["client"])
            table["gachaPoolClient"][0]["gachaPoolId"] = "STALE_REPLACEMENT"
            return httpx.Response(200, json=table)
        return httpx.Response(200, json=responses[url])

    mock_catalog_http(monkeypatch, handle)
    report = await catalog.sync()
    assert {CLIENT_CDN_URL, CHARACTER_CDN_URL} <= set(requests)
    assert report["sources"]["client"]["url"] == CLIENT_CDN_URL
    assert report["sources"]["operators"]["url"] == CHARACTER_CDN_URL
    assert len(Catalog(tmp_path).load().pools) == 100


@pytest.mark.parametrize("failure", ["all_sources", "prts", "stale_cdn", "mismatched_details"])
async def test_failed_sync_preserves_all_old_files_and_snapshot(tmp_path, monkeypatch, failure):
    catalog = Catalog(tmp_path)
    payloads, bodies, urls = publish_payloads()
    report = catalog.publish(payloads, bodies, urls)
    before = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    responses = source_responses(payloads)

    def handle(request):
        url = str(request.url)
        if failure == "all_sources" and url != DETAIL_URL:
            return httpx.Response(503)
        if failure == "prts" and url == DETAIL_URL:
            raise httpx.ReadTimeout("fixture", request=request)
        if failure == "stale_cdn":
            if url in {CLIENT_URL, CHARACTER_URL}:
                return httpx.Response(503)
            if url == CLIENT_CDN_URL:
                stale = copy.deepcopy(payloads["client"])
                stale["gachaPoolClient"][0]["gachaPoolId"] = "STALE_POOL"
                return httpx.Response(200, json=stale)
        if failure == "mismatched_details" and url == DETAIL_URL:
            detail = copy.deepcopy(payloads["details"])
            detail["gachaPoolClient"][0]["gachaPoolId"] = "UNRELATED_POOL"
            return httpx.Response(200, json=detail)
        return httpx.Response(200, json=responses[url])

    mock_catalog_http(monkeypatch, handle)
    with pytest.raises(UserError, match="^卡池更新失败。$"):
        await catalog.sync()
    assert not catalog.sync_lock.locked()
    after = {p.relative_to(tmp_path): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    assert after == before
    assert Catalog(tmp_path).load().version == report["version"]


async def test_prts_retries_before_publishing(tmp_path, monkeypatch):
    payloads, _, _ = publish_payloads()
    responses = source_responses(payloads)
    attempts = 0

    def handle(request):
        nonlocal attempts
        if str(request.url) == DETAIL_URL:
            attempts += 1
            if attempts == 1:
                return httpx.Response(200, json={})
        return httpx.Response(200, json=responses[str(request.url)])

    mock_catalog_http(monkeypatch, handle)
    assert (await Catalog(tmp_path).sync())["supported"] == 100
    assert attempts == 2


async def test_failed_first_sync_reports_no_snapshot(tmp_path, monkeypatch):
    mock_catalog_http(monkeypatch, lambda request: httpx.Response(503))
    with pytest.raises(UserError, match="^卡池更新失败。$"):
        await Catalog(tmp_path).sync()
    assert not (tmp_path / "current").exists()


async def test_sync_cancellation_releases_lock_without_fallback(tmp_path, monkeypatch):
    def handle(request):
        raise asyncio.CancelledError

    mock_catalog_http(monkeypatch, handle)
    catalog = Catalog(tmp_path)
    with pytest.raises(asyncio.CancelledError):
        await catalog.sync()
    assert not catalog.sync_lock.locked()
    assert not (tmp_path / "current").exists()


def test_pointer_write_failure_keeps_old_snapshot_and_cleans_temp(tmp_path, monkeypatch):
    catalog = Catalog(tmp_path)
    payloads, bodies, urls = publish_payloads()
    report = catalog.publish(payloads, bodies, urls)
    payloads["client"]["gachaPoolClient"][0]["guarantee5Avail"] = 0
    import os

    replace = os.replace

    def fail_pointer(source, destination):
        if destination == tmp_path / "current":
            raise OSError("fixture: disk failure")
        replace(source, destination)

    monkeypatch.setattr("ark_gacha.catalog.os.replace", fail_pointer)
    with pytest.raises(OSError):
        catalog.publish(payloads, bodies, urls)
    assert Catalog(tmp_path).load().version == report["version"]
    assert not list(tmp_path.rglob("*.tmp"))


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
    assert [p.id for p in Catalog.available(snapshot)] == [
        "EVENT_ORIGINAL",
        "EVENT_RERUN",
    ]


def test_future_pools_are_available_and_sort_is_stable(pool):
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
    assert [p.id for p in Catalog.available(snapshot)] == [
        "FUTURE",
        "A",
        "Z",
    ]
