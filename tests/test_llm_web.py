"""web_provider(날씨·식물 지식·뉴스) + factcheck 외부 문맥 게이트 테스트.

네트워크는 실호출하지 않는다 — urllib.request.urlopen을 monkeypatch로 대체한다.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

import potner_llm.web_provider as web_module
from potner_llm.factcheck import verify_response
from potner_llm.models import SensorSnapshot
from potner_llm.tools import ToolHub
from potner_llm.web_provider import WebProvider, create_web_provider


class _FakeResponse:
    def __init__(self, payload):
        self._raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _fake_urlopen(monkeypatch, payload, *, capture: dict | None = None):
    def fake(request, timeout=None):
        if capture is not None:
            capture["url"] = request.full_url
            capture["timeout"] = timeout
        if isinstance(payload, Exception):
            raise payload
        return _FakeResponse(payload)

    monkeypatch.setattr(web_module.urllib.request, "urlopen", fake)


def _provider(**overrides) -> WebProvider:
    kwargs = dict(latitude=37.5, longitude=127.04, timeout_seconds=8.0, news_limit=5)
    kwargs.update(overrides)
    return WebProvider(**kwargs)


# --- 날씨 ---

_WEATHER_BODY = {
    "current": {
        "temperature_2m": 3.4,
        "relative_humidity_2m": 40,
        "precipitation": 0.0,
        "weather_code": 3,
    },
    "daily": {
        "time": ["2026-08-04", "2026-08-05", "2026-08-06"],
        "temperature_2m_min": [1.0, -2.0, 0.5],
        "temperature_2m_max": [8.0, 5.0, 7.0],
        "precipitation_probability_max": [10, 60, 30],
        "weather_code": [0, 61, 2],
    },
}


def test_weather_success(monkeypatch):
    capture: dict = {}
    _fake_urlopen(monkeypatch, _WEATHER_BODY, capture=capture)

    result = _provider().weather()

    assert result["현재_바깥"]["기온_c"] == 3.4
    assert result["현재_바깥"]["하늘"] == "흐림"
    assert len(result["일별예보"]) == 3
    assert result["일별예보"][1] == {
        "날짜": "2026-08-05",
        "최저_c": -2.0,
        "최고_c": 5.0,
        "강수확률_pct": 60,
        "하늘": "약한 비",
    }
    assert "출처" in result
    assert "open-meteo.com" in capture["url"]
    assert capture["timeout"] == 8.0


def test_weather_failure_reuses_last_good(monkeypatch):
    provider = _provider()
    _fake_urlopen(monkeypatch, _WEATHER_BODY)
    provider.weather()

    _fake_urlopen(monkeypatch, urllib.error.URLError("down"))
    result = provider.weather()
    assert result["현재_바깥"]["기온_c"] == 3.4
    assert "안내" in result


def test_weather_failure_without_cache_returns_notice(monkeypatch):
    _fake_urlopen(monkeypatch, urllib.error.URLError("down"))
    result = _provider().weather()
    assert result["error"] == "weather_unavailable"
    assert "안내" in result


# --- 식물 지식 ---

_WIKI_BODY = {
    "query": {
        "pages": {
            "2": {"index": 2, "title": "황화 현상", "extract": "잎이 노랗게 변하는 현상. " * 60},
            "1": {"index": 1, "title": "스킨답서스", "extract": "덩굴성 관엽식물이다."},
        }
    }
}


def test_search_knowledge_orders_and_truncates(monkeypatch):
    _fake_urlopen(monkeypatch, _WIKI_BODY)

    result = _provider().search_knowledge("스킨답서스")

    assert [r["제목"] for r in result["결과"]] == ["스킨답서스", "황화 현상"]
    assert result["결과"][0]["요약"] == "덩굴성 관엽식물이다."
    assert len(result["결과"][1]["요약"]) == web_module.KNOWLEDGE_EXTRACT_MAX_CHARS


def test_search_knowledge_empty_query_short_circuits(monkeypatch):
    called = {}
    _fake_urlopen(monkeypatch, _WIKI_BODY, capture=called)

    result = _provider().search_knowledge("   ")
    assert result["error"] == "empty_query"
    assert "url" not in called  # 네트워크까지 가지 않는다


def test_search_knowledge_no_results(monkeypatch):
    _fake_urlopen(monkeypatch, {"query": {"pages": {}}})
    result = _provider().search_knowledge("없는검색어")
    assert result["결과"] == []
    assert "안내" in result


def test_search_knowledge_failure_returns_notice(monkeypatch):
    _fake_urlopen(monkeypatch, urllib.error.URLError("down"))
    result = _provider().search_knowledge("스킨답서스")
    assert result["error"] == "search_unavailable"


# --- 뉴스 ---

_RSS_BODY = (
    "<rss><channel>"
    + "".join(
        f"<item><title>헤드라인 {i}</title><source>언론사{i}</source></item>"
        for i in range(1, 8)
    )
    + "</channel></rss>"
).encode("utf-8")


def test_news_limits_headlines(monkeypatch):
    _fake_urlopen(monkeypatch, _RSS_BODY)

    result = _provider(news_limit=5).news()

    assert len(result["헤드라인"]) == 5
    assert result["헤드라인"][0] == {"제목": "헤드라인 1", "언론사": "언론사1"}


def test_news_failure_reuses_last_good(monkeypatch):
    provider = _provider()
    _fake_urlopen(monkeypatch, _RSS_BODY)
    provider.news()

    _fake_urlopen(monkeypatch, urllib.error.URLError("down"))
    result = provider.news()
    assert len(result["헤드라인"]) == 5
    assert "안내" in result


# --- 팩토리 ---

def test_create_web_provider_disabled_returns_none():
    assert create_web_provider({}) is None
    assert create_web_provider({"web": {"enabled": False, "latitude": 1, "longitude": 2}}) is None


def test_create_web_provider_builds_from_config():
    provider = create_web_provider(
        {"web": {"enabled": True, "latitude": 37.5, "longitude": 127.04,
                 "timeout_seconds": 3, "news_limit": 2}}
    )
    assert isinstance(provider, WebProvider)
    assert provider.latitude == 37.5
    assert provider.timeout_seconds == 3.0
    assert provider.news_limit == 2


def test_create_web_provider_requires_coordinates():
    with pytest.raises(ValueError):
        create_web_provider({"web": {"enabled": True}})
    with pytest.raises(ValueError):
        create_web_provider({"web": {"enabled": True, "latitude": "서울", "longitude": 127}})


# --- ToolHub 연동 ---

def _status_stub():
    raise AssertionError("웹 툴은 status를 조회하지 않는다")


class _HubEvents:
    def recent(self, limit=20):
        return []


def _hub(web) -> ToolHub:
    return ToolHub(get_status=_status_stub, event_store=_HubEvents(), web=web)


def test_tool_hub_dispatches_web_tools(monkeypatch):
    _fake_urlopen(monkeypatch, _WEATHER_BODY)
    result = _hub(_provider()).call("get_weather")
    assert result["현재_바깥"]["기온_c"] == 3.4

    _fake_urlopen(monkeypatch, _WIKI_BODY)
    result = _hub(_provider()).call("search_plant_knowledge", {"query": "스킨답서스"})
    assert result["결과"][0]["제목"] == "스킨답서스"

    _fake_urlopen(monkeypatch, _RSS_BODY)
    result = _hub(_provider()).call("get_news")
    assert result["헤드라인"]


def test_tool_hub_web_disabled_notice():
    for name in ("get_weather", "search_plant_knowledge", "get_news"):
        result = _hub(None).call(name, {"query": "x"})
        assert result["error"] == "web_disabled"


# --- factcheck 외부 문맥 게이트 ---

_SENSORS = SensorSnapshot(soil=50.0, temp=26.0, humidity=55.0, light=800.0, co2=None)


def test_factcheck_skips_forecast_temperature():
    result = verify_response("예보로는 내일 3도까지 떨어진대. 바깥은 춥겠다!", _SENSORS)
    assert result.ok


def test_factcheck_skips_weather_percent():
    result = verify_response("내일 비 올 확률이 60%래. 창문 닫아줘!", _SENSORS)
    assert result.ok


def test_factcheck_skips_knowledge_range():
    result = verify_response("스킨답서스 적정 온도는 18도에서 24도 사이야.", _SENSORS)
    assert result.ok


def test_factcheck_still_catches_sensor_lie():
    result = verify_response("지금 내 온도는 3도야.", _SENSORS)
    assert not result.ok
    assert "sensor_mismatch:temp" in result.issue_codes


def test_factcheck_still_passes_true_sensor_claim():
    result = verify_response("지금 26도라서 딱 좋아.", _SENSORS)
    assert result.ok


def test_factcheck_skips_dry_claim_about_weather():
    result = verify_response("요즘 바깥 날씨가 아주 건조하대.", _SENSORS)
    assert result.ok


def test_factcheck_still_catches_false_dry_claim():
    wet = SensorSnapshot(soil=75.0, temp=26.0, humidity=55.0, light=800.0, co2=None)
    result = verify_response("나 지금 흙이 다 말랐어. 목말라!", wet)
    assert not result.ok
    assert "state_conflict:soil" in result.issue_codes
