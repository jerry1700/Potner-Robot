"""센서 데이터 조회 provider·툴·프롬프트 렌더링 단위 테스트 (ROS/API 키 불필요).

네트워크는 실호출하지 않는다 — HTTP 소스는 urllib.request.urlopen을
monkeypatch로 대체한다.
"""

from __future__ import annotations

import json
import urllib.error

import pytest

from potner_llm import sensor_provider as sensor_provider_module
from potner_llm.config import ServiceSettings
from potner_llm.events import EventStore
from potner_llm.models import PlantProfile, SensorSnapshot
from potner_llm.prompt_builder import build_system_prompt
from potner_llm.sensor_provider import (
    CallbackSensorSource,
    FileSensorSource,
    HttpSensorSource,
    SensorDataProvider,
    SpringSensorSource,
    build_plant_status,
    classify_metric,
    create_sensor_provider,
    parse_snapshot,
)
from potner_llm.service import PlantChatService
from potner_llm.tools import TOOL_DEFINITIONS, ToolHub

SETTINGS = ServiceSettings()


# --- 판정: 다양한 센서 상태 → 라벨 ---


@pytest.mark.parametrize(
    "name,value,level,label",
    [
        ("soil", 18.0, "low", "건조"),
        ("soil", 50.0, "normal", "적정"),
        ("soil", 85.0, "high", "과습"),
        ("soil", None, "unknown", "미측정"),
        ("temperature", 10.0, "low", "저온"),
        ("temperature", 24.0, "normal", "적정"),
        ("temperature", 35.0, "high", "고온"),
        ("humidity", 20.0, "low", "건조"),
        ("humidity", 90.0, "high", "다습"),
        ("light", 30.0, "low", "어두움"),
        ("light", 800.0, "normal", "적정"),
        ("light", 50000.0, "high", "강한 빛"),
    ],
)
def test_classify_metric_levels(name, value, level, label):
    metric = classify_metric(name, value)
    assert metric.level == level
    assert metric.label_ko == label
    assert metric.value == value


def test_classify_metric_rejects_unknown_sensor_name():
    with pytest.raises(ValueError):
        classify_metric("ph", 7.0)


def test_build_plant_status_normal_is_not_attention():
    status = build_plant_status(
        SensorSnapshot(soil=50.0, temp=24.0, humidity=55.0, light=800.0),
        timestamp="2026-07-31T10:00:00",
    )
    assert status.needs_attention is False
    assert status.summary_ko == "전반적으로 양호해요"
    assert status.timestamp == "2026-07-31T10:00:00"


def test_build_plant_status_dry_soil_needs_attention():
    status = build_plant_status(SensorSnapshot(soil=15.0, temp=24.0, humidity=55.0, light=800.0))
    assert status.needs_attention is True
    assert status.soil.level == "low"
    assert "물이 필요해요" in status.summary_ko


def test_build_plant_status_multiple_issues_are_all_reported():
    status = build_plant_status(SensorSnapshot(soil=90.0, temp=35.0, humidity=55.0, light=10.0))
    assert status.needs_attention is True
    assert "과습" in status.summary_ko
    assert "온도가 너무 높아요" in status.summary_ko
    assert "빛이 부족해요" in status.summary_ko


def test_build_plant_status_broken_soil_sensor_is_unknown_not_crash():
    # 토양 수분 센서 고장(재구매 대기) — soil 결측이 실기의 기본 상태다.
    status = build_plant_status(SensorSnapshot(soil=None, temp=24.0, humidity=55.0, light=800.0))
    assert status.soil.level == "unknown"
    assert status.soil.label_ko == "미측정"
    assert status.needs_attention is False


def test_build_plant_status_all_missing_says_no_data():
    status = build_plant_status(None)
    assert status.needs_attention is False
    assert "센서 데이터가 없어서" in status.summary_ko


# --- 원시 데이터 정규화 ---


def test_parse_snapshot_maps_aliases_and_strings():
    snapshot = parse_snapshot(
        {"soil_moisture": "42.5", "temperature": 26, "lux": 800, "humid": 55.0, "co2": 600}
    )
    assert snapshot.soil == 42.5
    assert snapshot.temp == 26.0
    assert snapshot.light == 800.0
    assert snapshot.humidity == 55.0
    assert snapshot.co2 == 600.0


def test_parse_snapshot_drops_bad_values_but_keeps_the_rest():
    snapshot = parse_snapshot({"soil": "not-a-number", "temp": 24.0, "unknown_key": 1})
    assert snapshot.soil is None
    assert snapshot.temp == 24.0


def test_parse_snapshot_rejects_non_dict():
    with pytest.raises(TypeError):
        parse_snapshot([1, 2, 3])


# --- 소스 어댑터 ---


def test_callback_source_accepts_dict_and_snapshot():
    from_dict = CallbackSensorSource(lambda: {"soil": 40.0}).read()
    assert from_dict.soil == 40.0

    fixed = SensorSnapshot(temp=22.0)
    from_snapshot = CallbackSensorSource(lambda: fixed).read()
    assert from_snapshot is fixed

    assert CallbackSensorSource(lambda: None).read() is None


def test_file_source_reads_json(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"soil": 33.0, "temp": 25.5}), encoding="utf-8")
    snapshot = FileSensorSource(path).read()
    assert snapshot.soil == 33.0
    assert snapshot.temp == 25.5


def test_http_source_fetches_json(monkeypatch):
    captured = {}

    class _FakeResponse:
        def read(self):
            return json.dumps({"soil": 41.0, "light": 900}).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_urlopen(req, timeout=5.0):
        captured["url"] = req.full_url
        captured["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(sensor_provider_module.urllib.request, "urlopen", fake_urlopen)

    snapshot = HttpSensorSource("https://example.test/sensors", timeout_seconds=3.0).read()
    assert snapshot.soil == 41.0
    assert snapshot.light == 900.0
    assert captured["url"] == "https://example.test/sensors"
    assert captured["timeout"] == 3.0


# --- SpringSensorSource: 서버 응답(배열) → SensorSnapshot(평평) 재구성 ---


def _fake_spring_response(monkeypatch, body, *, capture=None):
    class _FakeResponse:
        def read(self):
            return json.dumps(body).encode("utf-8")

        def __enter__(self):
            return self

        def __exit__(self, *exc_info):
            return False

    def fake_urlopen(req, timeout=5.0):
        if capture is not None:
            capture["url"] = req.full_url
            capture["headers"] = dict(req.header_items())
            capture["timeout"] = timeout
        return _FakeResponse()

    monkeypatch.setattr(sensor_provider_module.urllib.request, "urlopen", fake_urlopen)


def test_spring_source_maps_sensor_types_to_snapshot(monkeypatch):
    _fake_spring_response(
        monkeypatch,
        {
            "plantId": "p1",
            "sensors": [
                {"sensorType": "TEMPERATURE", "value": 23.5, "status": "NORMAL"},
                {"sensorType": "HUMIDITY", "value": 55.0, "status": "NORMAL"},
                {"sensorType": "SOIL_MOISTURE", "value": 40.0, "status": "LOW"},
                {"sensorType": "ILLUMINANCE", "value": 800.0, "status": "NOT_APPLICABLE"},
            ],
        },
    )
    source = SpringSensorSource("https://example.test/sensors/current", token_provider=lambda: "tok")
    snapshot = source.read()

    assert snapshot.temp == 23.5
    assert snapshot.humidity == 55.0
    assert snapshot.soil == 40.0
    assert snapshot.light == 800.0


def test_spring_source_no_data_becomes_none(monkeypatch):
    """NO_DATA는 값이 있어도 싣지 않는다 — 아직 안 잰 것과 0을 구분해야 한다."""
    _fake_spring_response(
        monkeypatch,
        {"sensors": [{"sensorType": "TEMPERATURE", "value": 0.0, "status": "NO_DATA"}]},
    )
    snapshot = SpringSensorSource("https://example.test", token_provider=lambda: "tok").read()
    assert snapshot.temp is None


def test_spring_source_stale_value_is_still_used(monkeypatch):
    """STALE도 마지막 실측값은 유효하다 — 로봇 쪽 절대 임계값 판정이 다시 하므로 중복 무방."""
    _fake_spring_response(
        monkeypatch,
        {"sensors": [{"sensorType": "HUMIDITY", "value": 61.0, "status": "STALE"}]},
    )
    snapshot = SpringSensorSource("https://example.test", token_provider=lambda: "tok").read()
    assert snapshot.humidity == 61.0


def test_spring_source_ignores_unknown_sensor_types(monkeypatch):
    """서버가 나중에 센서 종류를 늘려도 모르는 타입은 무시하고 나머지는 정상 처리."""
    _fake_spring_response(
        monkeypatch,
        {
            "sensors": [
                {"sensorType": "TEMPERATURE", "value": 22.0, "status": "NORMAL"},
                {"sensorType": "CO2_PPM", "value": 800.0, "status": "NORMAL"},
            ]
        },
    )
    snapshot = SpringSensorSource("https://example.test", token_provider=lambda: "tok").read()
    assert snapshot.temp == 22.0


def test_spring_source_authorization_header_gets_bearer_prefix(monkeypatch):
    captured = {}
    _fake_spring_response(monkeypatch, {"sensors": []}, capture=captured)

    SpringSensorSource(
        "https://example.test/sensors/current",
        token_provider=lambda: "secret-token",
        token_header="Authorization",
    ).read()

    assert captured["headers"]["Authorization"] == "Bearer secret-token"
    assert captured["url"] == "https://example.test/sensors/current"


def test_spring_source_device_token_header_has_no_bearer_prefix(monkeypatch):
    """장치 토큰 방식(X-Device-Token)은 라즈베리 사진 업로드와 같은 관례 — Bearer를 안 붙인다."""
    captured = {}
    _fake_spring_response(monkeypatch, {"sensors": []}, capture=captured)

    SpringSensorSource(
        "https://example.test/sensors/current",
        token_provider=lambda: "device-secret",
        token_header="X-Device-Token",
    ).read()

    assert captured["headers"]["X-device-token"] == "device-secret"


def test_spring_source_failure_is_absorbed_by_provider(monkeypatch):
    def fake_urlopen(req, timeout=5.0):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(sensor_provider_module.urllib.request, "urlopen", fake_urlopen)
    provider = SensorDataProvider(
        SpringSensorSource("https://example.test", token_provider=lambda: "tok")
    )
    assert provider.snapshot() is None
    assert provider.status().temperature.level == "unknown"


def test_create_sensor_provider_spring_kind(monkeypatch):
    monkeypatch.setenv("POTNER_SENSOR_TOKEN", "abc123")
    captured = {}
    _fake_spring_response(
        monkeypatch,
        {"sensors": [{"sensorType": "TEMPERATURE", "value": 19.0, "status": "NORMAL"}]},
        capture=captured,
    )

    provider = create_sensor_provider(
        {
            "sensor": {
                "source": "spring",
                "url": "https://example.test/sensors/current",
                "token_env": "POTNER_SENSOR_TOKEN",
                "token_header": "X-Device-Token",
            }
        }
    )
    assert provider.snapshot().temp == 19.0
    assert captured["headers"]["X-device-token"] == "abc123"


def test_create_sensor_provider_spring_requires_url_and_token_env():
    with pytest.raises(ValueError):
        create_sensor_provider({"sensor": {"source": "spring"}})
    with pytest.raises(ValueError):
        create_sensor_provider({"sensor": {"source": "spring", "url": "https://example.test"}})


def test_create_sensor_provider_spring_missing_env_value_raises(monkeypatch):
    """토큰 env가 선언은 됐지만 실제로 비어 있으면 조용히 넘어가지 않는다."""
    monkeypatch.delenv("POTNER_SENSOR_TOKEN_MISSING", raising=False)
    provider = create_sensor_provider(
        {
            "sensor": {
                "source": "spring",
                "url": "https://example.test",
                "token_env": "POTNER_SENSOR_TOKEN_MISSING",
            }
        }
    )
    # provider 생성 자체는 성공 — 호출 시점(read)에 토큰이 비어 있으면 실패한다.
    assert provider.snapshot() is None  # RuntimeError가 provider 안에서 흡수됨


# --- provider: 실패 흡수 + 최신값 반영 ---


def test_provider_returns_none_when_file_missing(tmp_path):
    provider = SensorDataProvider(FileSensorSource(tmp_path / "no-such.json"))
    assert provider.snapshot() is None  # 예외가 새면 안 된다


def test_provider_status_never_raises_on_source_failure(tmp_path):
    provider = SensorDataProvider(FileSensorSource(tmp_path / "no-such.json"))
    status = provider.status()
    assert status.soil.level == "unknown"
    assert status.needs_attention is False


def test_provider_reflects_latest_file_values(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"soil": 20.0}), encoding="utf-8")
    provider = SensorDataProvider(FileSensorSource(path))
    assert provider.snapshot().soil == 20.0

    path.write_text(json.dumps({"soil": 65.0}), encoding="utf-8")
    assert provider.snapshot().soil == 65.0  # 캐시가 아니라 최신값


def test_provider_reuses_last_good_snapshot_on_failure(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"soil": 44.0}), encoding="utf-8")
    provider = SensorDataProvider(FileSensorSource(path))
    assert provider.snapshot().soil == 44.0

    path.write_text("{{{broken json", encoding="utf-8")
    stale = provider.snapshot()
    assert stale is not None and stale.soil == 44.0


def test_provider_http_failure_is_absorbed(monkeypatch):
    def fake_urlopen(req, timeout=5.0):
        raise urllib.error.URLError("no route to host")

    monkeypatch.setattr(sensor_provider_module.urllib.request, "urlopen", fake_urlopen)
    provider = SensorDataProvider(HttpSensorSource("https://example.test/sensors"))
    assert provider.snapshot() is None
    assert provider.status().soil.level == "unknown"


def test_provider_logs_success_and_failure(tmp_path, caplog):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"soil": 44.0}), encoding="utf-8")
    provider = SensorDataProvider(FileSensorSource(path))

    with caplog.at_level("INFO", logger="potner_llm.sensor_provider"):
        provider.snapshot()
    assert any("센서 조회 성공" in r.message for r in caplog.records)

    caplog.clear()
    path.unlink()
    with caplog.at_level("WARNING", logger="potner_llm.sensor_provider"):
        provider.snapshot()
    assert any("센서 조회 실패" in r.message for r in caplog.records)


def test_create_sensor_provider_from_config(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"temp": 21.0}), encoding="utf-8")

    assert create_sensor_provider({}) is None
    assert create_sensor_provider({"sensor": {"source": "none"}}) is None

    provider = create_sensor_provider({"sensor": {"source": "file", "path": str(path)}})
    assert provider.snapshot().temp == 21.0

    with pytest.raises(ValueError):
        create_sensor_provider({"sensor": {"source": "file"}})
    with pytest.raises(ValueError):
        create_sensor_provider({"sensor": {"source": "http"}})
    with pytest.raises(ValueError):
        create_sensor_provider({"sensor": {"source": "carrier-pigeon"}})


# --- 프롬프트 렌더링 ---


def test_prompt_renders_values_with_labels():
    sensors = SensorSnapshot(soil=18.0, temp=35.0, humidity=55.0, light=800.0)
    prompt = build_system_prompt(PlantProfile(), sensors, SETTINGS)
    assert "18% (건조)" in prompt
    assert "35°C (고온)" in prompt
    assert "55% (적정)" in prompt
    assert "800 lux (적정)" in prompt
    assert "주의 상태" in prompt
    assert "물이 필요해요" in prompt


def test_prompt_normal_state_has_no_attention_line():
    sensors = SensorSnapshot(soil=50.0, temp=24.0, humidity=55.0, light=800.0)
    prompt = build_system_prompt(PlantProfile(), sensors, SETTINGS)
    assert "주의 상태" not in prompt


def test_prompt_without_sensors_still_says_no_data():
    prompt = build_system_prompt(PlantProfile(), SensorSnapshot(), SETTINGS)
    assert "센서 데이터 없음" in prompt


# --- ToolHub: get_sensor_data ---


def _demo_status_for_tools():
    return build_plant_status(
        SensorSnapshot(soil=20.0, temp=25.0, humidity=50.0, light=500.0),
        timestamp="2026-07-31T10:00:00",
    )


def test_tool_definitions_include_sensor_tool():
    names = [t["function"]["name"] for t in TOOL_DEFINITIONS]
    assert "get_sensor_data" in names
    assert "get_plant_status" in names  # 기존 툴 유지


def test_tool_hub_get_sensor_data_with_provider(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    hub = ToolHub(
        get_status=_demo_status_for_tools,
        event_store=store,
        get_sensors=lambda: SensorSnapshot(soil=None, temp=31.0, humidity=45.0, light=700.0),
    )
    result = hub.call("get_sensor_data")
    assert result["출처"] == "sensor"
    assert result["측정값"]["토양수분_pct"] is None  # 고장난 센서는 null로 정직하게
    assert result["측정값"]["온도_c"] == 31.0
    assert result["판정"]["온도_c"] == "고온"
    assert result["판정"]["토양수분_pct"] == "미측정"
    json.dumps(result, ensure_ascii=False)  # LLM tool 결과로 직렬화 가능해야 한다


def test_tool_hub_get_sensor_data_without_provider_falls_back_to_status(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    hub = ToolHub(get_status=_demo_status_for_tools, event_store=store)
    result = hub.call("get_sensor_data")
    assert result["출처"] == "status"
    assert result["측정값"]["토양수분_pct"] == 20.0


def test_tool_hub_never_raises_on_provider_failure(tmp_path):
    def broken_sensors():
        raise ConnectionError("MQTT down")

    def broken_status():
        raise RuntimeError("status down")

    store = EventStore(tmp_path / "events.jsonl")
    hub = ToolHub(get_status=broken_status, event_store=store, get_sensors=broken_sensors)
    result = hub.call("get_sensor_data")
    assert result["error"].startswith("tool_failed")

    result = hub.call("get_plant_status")
    assert result["error"].startswith("tool_failed")


def test_tool_hub_unknown_tool_returns_error_payload_not_exception(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    hub = ToolHub(get_status=_demo_status_for_tools, event_store=store)
    result = hub.call("water_the_plant")
    assert result["error"] == "unknown_tool:water_the_plant"
    assert "get_sensor_data" in result["사용가능한_툴"]


# --- 응답 파이프라인: 최신 센서가 LLM 프롬프트/응답에 반영되는지 ---


class _CapturingClient:
    """LLM 대역 — 받은 system 프롬프트를 기록하고 고정 응답을 돌려준다."""

    model = "stub-model"

    def __init__(self, reply: str):
        self._reply = reply
        self.last_system = None

    def complete(self, user_prompt: str, *, system: str, temperature: float = 0.7):
        self.last_system = system
        return self._reply


def test_answer_uses_latest_sensor_snapshot_from_provider(tmp_path):
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"soil": 18.0, "temp": 26.0}), encoding="utf-8")
    provider = SensorDataProvider(FileSensorSource(path))

    stub = _CapturingClient(reply="흙이 말라서 목이 말라. 물 조금만 부탁해!")
    service = PlantChatService(client=stub, sensor_provider=provider.snapshot)

    result = service.answer("물 줘야 해?")
    assert result["success"] is True
    assert result["sensor_summary"]["soil"] == 18.0
    assert "18% (건조)" in stub.last_system

    # 센서 파일이 갱신되면 다음 질문의 프롬프트에 최신값이 실린다.
    path.write_text(json.dumps({"soil": 60.0, "temp": 26.0}), encoding="utf-8")
    result = service.answer("지금은 어때?")
    assert result["sensor_summary"]["soil"] == 60.0
    assert "60% (적정)" in stub.last_system


def test_answer_survives_sensor_source_failure(tmp_path):
    provider = SensorDataProvider(FileSensorSource(tmp_path / "no-such.json"))
    stub = _CapturingClient(reply="나는 잘 지내고 있어!")
    service = PlantChatService(client=stub, sensor_provider=provider.snapshot)

    result = service.answer("잘 지내?")
    assert result["success"] is True  # 조회 실패해도 기본값으로 답한다
    assert result["sensor_summary"]["soil"] == 50.0  # config 기본값


def test_dialogue_stack_tool_call_reads_latest_sensors(tmp_path):
    """chat_with_tools 경로: 툴 결과에 provider의 최신 센서값이 실리는지."""
    path = tmp_path / "sensors.json"
    path.write_text(json.dumps({"soil": 22.0, "temp": 24.0, "humidity": 50.0, "light": 400.0}), encoding="utf-8")
    provider = SensorDataProvider(FileSensorSource(path))

    store = EventStore(tmp_path / "events.jsonl")
    hub = ToolHub(get_status=provider.status, event_store=store, get_sensors=provider.snapshot)

    result = hub.call("get_sensor_data")
    assert result["측정값"]["토양수분_pct"] == 22.0
    assert result["판정"]["토양수분_pct"] == "건조"

    status = hub.call("get_plant_status")
    assert status["주의필요"] is True
    assert "물이 필요해요" in status["요약"]
