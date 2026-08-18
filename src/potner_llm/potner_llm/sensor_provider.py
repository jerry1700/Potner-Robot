"""센서 데이터 조회 provider — 콜백/파일/HTTP 소스를 한 인터페이스로 묶는다.

실기 MQTT/ROS 연동 전의 어댑터 계층. potner_llm 안에서는 rclpy를 import하지
않는다 — ROS/MQTT 연동 시에도 노드 쪽에서 콜백(CallbackSensorSource)으로
주입한다. 센서 상태 모델이 두 벌(dialogue 스택의 PlantStatus 라벨,
파이프라인 스택의 SensorSnapshot 원시값)이라 provider가 둘 다 내놓는다:

    provider = SensorDataProvider(FileSensorSource(path))
    PlantChatService(sensor_provider=provider.snapshot)   # 파이프라인 스택
    DialogueService(get_status=provider.status, ...)      # dialogue 스택

조회 실패는 절대 밖으로 던지지 않는다 — snapshot()은 None(또는 마지막
성공값), status()는 unknown 라벨로 채운 PlantStatus를 돌려준다.
"""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from .models import SensorSnapshot
from .status import MetricLevel, PlantStatus

logger = logging.getLogger(__name__)

# --- 판정 임계값 ---
# 스킨답서스 기준의 보수적인 범위. 종별 세분화는 profile 연동 후 과제.
SOIL_DRY_BELOW = 30.0        # %
SOIL_WET_ABOVE = 70.0        # %
TEMP_COLD_BELOW = 15.0       # °C
TEMP_HOT_ABOVE = 30.0        # °C
HUMIDITY_LOW_BELOW = 30.0    # %
HUMIDITY_HIGH_ABOVE = 70.0   # %
LIGHT_DARK_BELOW = 100.0     # lux
LIGHT_HARSH_ABOVE = 20000.0  # lux

# 소스마다 키 이름이 제각각이라(MQTT 페이로드/파일/데모 스텁) 정규화한다.
_FIELD_ALIASES: dict[str, str] = {
    "soil": "soil",
    "soil_moisture": "soil",
    "soilmoisture": "soil",
    "moisture": "soil",
    "temp": "temp",
    "temperature": "temp",
    "humidity": "humidity",
    "humid": "humidity",
    "light": "light",
    "lux": "light",
    "illuminance": "light",
    "co2": "co2",
    "photo_summary": "photo_summary",
}

_NUMERIC_FIELDS = ("soil", "temp", "humidity", "light", "co2")

# Spring 서버 센서 조회 API(SensorType enum)의 값 → _FIELD_ALIASES가 아는 별칭.
# 서버 응답이 센서별 배열이라(각 항목이 sensorType/value/status를 가짐), 이
# 표로 평평한 dict를 만들어 parse_snapshot에 그대로 넘긴다.
_SPRING_SENSOR_TYPE_TO_ALIAS: dict[str, str] = {
    "TEMPERATURE": "temperature",
    "HUMIDITY": "humidity",
    "SOIL_MOISTURE": "soil_moisture",
    "ILLUMINANCE": "illuminance",
}

# 라벨/주의 문구: (low 라벨, high 라벨, low 문구, high 문구)
_LEVEL_TEXT: dict[str, tuple[str, str, str, str]] = {
    "soil": ("건조", "과습", "토양이 건조해서 물이 필요해요", "토양이 과습 상태예요"),
    "temperature": ("저온", "고온", "온도가 너무 낮아요", "온도가 너무 높아요"),
    "humidity": ("건조", "다습", "공기가 건조해요", "습도가 너무 높아요"),
    "light": ("어두움", "강한 빛", "빛이 부족해요", "빛이 너무 강해요"),
}

_THRESHOLDS: dict[str, tuple[float, float]] = {
    "soil": (SOIL_DRY_BELOW, SOIL_WET_ABOVE),
    "temperature": (TEMP_COLD_BELOW, TEMP_HOT_ABOVE),
    "humidity": (HUMIDITY_LOW_BELOW, HUMIDITY_HIGH_ABOVE),
    "light": (LIGHT_DARK_BELOW, LIGHT_HARSH_ABOVE),
}


# --- 판정 (SensorSnapshot 원시값 → MetricLevel/PlantStatus) ---


def classify_metric(name: str, value: Optional[float]) -> MetricLevel:
    """원시 센서값 하나를 low/normal/high/unknown 라벨로 판정한다.

    Args:
        name: "soil" | "temperature" | "humidity" | "light"
        value: 원시 측정값 (soil/humidity는 %, temperature는 °C, light는 lux)
    """
    if name not in _THRESHOLDS:
        raise ValueError(f"판정 임계값이 정의되지 않은 센서: {name}")
    if value is None:
        return MetricLevel(name=name, value=None, level="unknown", label_ko="미측정")
    low_bound, high_bound = _THRESHOLDS[name]
    low_label, high_label, _, _ = _LEVEL_TEXT[name]
    if value < low_bound:
        return MetricLevel(name=name, value=value, level="low", label_ko=low_label)
    if value > high_bound:
        return MetricLevel(name=name, value=value, level="high", label_ko=high_label)
    return MetricLevel(name=name, value=value, level="normal", label_ko="적정")


def attention_messages(snapshot: SensorSnapshot) -> list[str]:
    """비정상(low/high) 센서에 대한 한국어 주의 문구 목록."""
    pairs = (
        ("soil", snapshot.soil),
        ("temperature", snapshot.temp),
        ("humidity", snapshot.humidity),
        ("light", snapshot.light),
    )
    messages: list[str] = []
    for name, value in pairs:
        metric = classify_metric(name, value)
        if metric.level == "low":
            messages.append(_LEVEL_TEXT[name][2])
        elif metric.level == "high":
            messages.append(_LEVEL_TEXT[name][3])
    return messages


def build_plant_status(
    snapshot: Optional[SensorSnapshot], *, timestamp: Optional[str] = None
) -> PlantStatus:
    """SensorSnapshot(원시값)을 dialogue 스택의 PlantStatus(라벨)로 변환한다."""
    snapshot = snapshot or SensorSnapshot()
    soil = classify_metric("soil", snapshot.soil)
    temperature = classify_metric("temperature", snapshot.temp)
    humidity = classify_metric("humidity", snapshot.humidity)
    light = classify_metric("light", snapshot.light)

    issues = attention_messages(snapshot)
    metrics = (soil, temperature, humidity, light)
    if issues:
        summary_ko = ", ".join(issues)
    elif all(m.level == "unknown" for m in metrics):
        summary_ko = "센서 데이터가 없어서 상태를 알 수 없어요"
    else:
        summary_ko = "전반적으로 양호해요"

    return PlantStatus(
        timestamp=timestamp or datetime.now().astimezone().isoformat(timespec="seconds"),
        soil=soil,
        temperature=temperature,
        humidity=humidity,
        light=light,
        summary_ko=summary_ko,
        needs_attention=bool(issues),
    )


def parse_snapshot(raw: Any) -> SensorSnapshot:
    """소스가 준 원시 데이터(dict 또는 SensorSnapshot)를 SensorSnapshot으로 정규화한다.

    숫자로 못 바꾸는 값은 버린다(None 처리) — 소스 한 필드가 깨졌다고
    나머지 센서까지 못 쓰게 되면 안 되기 때문이다.
    """
    if isinstance(raw, SensorSnapshot):
        return raw
    if not isinstance(raw, dict):
        raise TypeError(f"센서 데이터는 dict 또는 SensorSnapshot이어야 함: {type(raw).__name__}")

    values: dict[str, Any] = {}
    for key, value in raw.items():
        field = _FIELD_ALIASES.get(str(key).lower())
        if field is None or value is None:
            continue
        if field == "photo_summary":
            values[field] = str(value)
            continue
        try:
            values[field] = float(value)
        except (TypeError, ValueError):
            logger.warning(f"센서 값 파싱 실패 — 해당 필드만 무시: {key}={value!r}")
    return SensorSnapshot(**values)


# --- 소스 어댑터 ---


class CallbackSensorSource:
    """이미 최신값을 들고 있는 쪽(ROS 노드, MQTT 구독자 등)이 콜백으로 주입하는 소스."""

    def __init__(self, callback: Callable[[], Any]) -> None:
        self._callback = callback

    def read(self) -> Optional[SensorSnapshot]:
        raw = self._callback()
        if raw is None:
            return None
        return parse_snapshot(raw)

    def describe(self) -> str:
        return "callback"


class FileSensorSource:
    """센서 수집 프로세스가 주기적으로 덮어쓰는 JSON 파일을 읽는 소스.

    예: {"soil": 42.0, "temp": 26.1, "humidity": 55.0, "light": 800.0}
    """

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def read(self) -> Optional[SensorSnapshot]:
        raw = json.loads(self.path.read_text(encoding="utf-8"))
        return parse_snapshot(raw)

    def describe(self) -> str:
        return f"file:{self.path}"


class HttpSensorSource:
    """센서 API(GET, JSON 응답)에서 최신값을 받아오는 소스."""

    def __init__(self, url: str, *, timeout_seconds: float = 5.0) -> None:
        self.url = url
        self.timeout_seconds = timeout_seconds

    def read(self) -> Optional[SensorSnapshot]:
        request = urllib.request.Request(self.url, headers={"Accept": "application/json"})
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            raw = json.loads(response.read().decode("utf-8"))
        return parse_snapshot(raw)

    def describe(self) -> str:
        return f"http:{self.url}"


class SpringSensorSource:
    """Spring 서버의 센서 조회 API에서 최신값을 받아오는 소스.

    응답이 HttpSensorSource처럼 평평한 dict가 아니라
    ``{"sensors": [{"sensorType": "TEMPERATURE", "value": 23.5, "status": "NORMAL"}, ...]}``
    형태라, parse_snapshot이 기대하는 평평한 dict로 먼저 재구성한다.
    ``status: "NO_DATA"``인 항목은 값을 안 싣는다(=None) — 나머지(STALE 포함)는
    value를 그대로 쓴다. 서버 판정(LOW/NORMAL/HIGH)과 별개로 로봇 쪽
    classify_metric이 절대 임계값으로 다시 판정하므로 중복돼도 문제없다.

    인증 헤더 이름을 열어둔 이유는 이 API의 최종 인증 방식이 아직 정해지지
    않았기 때문이다(docs/SERVER_REQUEST_SENSOR_AUTH.md 참고). 사용자
    JWT 방식이면 token_header="Authorization"(기본값, "Bearer " 접두 자동
    부착), 장치 토큰 방식(라즈베리 사진 업로드의 X-Device-Token과 동일
    계열)이면 token_header="X-Device-Token"으로 config만 바꾸면 된다.
    """

    def __init__(
        self,
        url: str,
        *,
        token_provider: Callable[[], str],
        token_header: str = "Authorization",
        timeout_seconds: float = 5.0,
    ) -> None:
        self.url = url
        self.token_provider = token_provider
        self.token_header = token_header
        self.timeout_seconds = timeout_seconds

    def read(self) -> Optional[SensorSnapshot]:
        token = self.token_provider()
        header_value = f"Bearer {token}" if self.token_header == "Authorization" else token
        request = urllib.request.Request(
            self.url,
            headers={"Accept": "application/json", self.token_header: header_value},
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            body = json.loads(response.read().decode("utf-8"))

        flat: dict[str, Any] = {}
        for item in body.get("sensors", []) if isinstance(body, dict) else []:
            alias = _SPRING_SENSOR_TYPE_TO_ALIAS.get(item.get("sensorType"))
            if alias is None or item.get("status") == "NO_DATA":
                continue
            flat[alias] = item.get("value")
        return parse_snapshot(flat)

    def describe(self) -> str:
        return f"spring:{self.url}"


# --- Provider (실패 흡수 + 양쪽 스택 인터페이스) ---


class SensorDataProvider:
    """소스에서 센서를 읽되, 실패를 절대 밖으로 던지지 않는 파사드.

    소스 실패 시 마지막 성공 스냅샷을 재사용한다(stale이 아예 없는 것보다
    낫다 — 값 판정 라벨은 그대로 유효하고, 없으면 None을 준다).
    """

    def __init__(self, source: Any) -> None:
        self._source = source
        self._last_good: Optional[SensorSnapshot] = None

    def snapshot(self) -> Optional[SensorSnapshot]:
        """최신 SensorSnapshot 또는 None. PlantChatService.sensor_provider용."""
        try:
            snapshot = self._source.read()
        except Exception as exc:
            if self._last_good is not None:
                logger.warning(
                    f"센서 조회 실패 ({self._describe()}): {exc} — 마지막 성공값 재사용"
                )
                return self._last_good
            logger.warning(f"센서 조회 실패 ({self._describe()}): {exc} — 데이터 없음")
            return None

        if snapshot is None:
            logger.info(f"센서 조회 결과 없음 ({self._describe()})")
            return self._last_good
        self._last_good = snapshot
        logger.info(f"센서 조회 성공 ({self._describe()}): {snapshot.summary_dict()}")
        return snapshot

    def status(self) -> PlantStatus:
        """최신 PlantStatus (조회 실패 시 unknown 라벨). DialogueService.get_status용."""
        return build_plant_status(self.snapshot())

    def _describe(self) -> str:
        describe = getattr(self._source, "describe", None)
        return describe() if callable(describe) else type(self._source).__name__


def create_sensor_provider(config: dict[str, Any] | None = None) -> Optional[SensorDataProvider]:
    """config(llm.yaml의 sensor 섹션)에서 provider를 만든다.

    config 예시::

        sensor:
          source: file          # file | http | spring | none
          path: data/sensors.json
          url: http://localhost:8000/sensors
          timeout_seconds: 5
          # spring일 때 추가로 필요:
          token_env: POTNER_SENSOR_TOKEN   # 값은 환경변수로, yaml엔 이름만
          token_header: Authorization      # 또는 X-Device-Token

    source가 없거나 none이면 None을 돌려준다(호출부가 기본 동작 유지).
    잘못된 설정은 조용히 넘기지 않고 시작 시점에 ValueError로 알린다 —
    conversation_backend와 같은 정책이다.
    """
    sensor_cfg = (config or {}).get("sensor") or {}
    kind = str(sensor_cfg.get("source", "none")).lower()
    if kind in ("", "none"):
        return None
    if kind == "file":
        path = sensor_cfg.get("path")
        if not path:
            raise ValueError("sensor.source=file에는 sensor.path가 필요하다")
        return SensorDataProvider(FileSensorSource(path))
    if kind == "http":
        url = sensor_cfg.get("url")
        if not url:
            raise ValueError("sensor.source=http에는 sensor.url이 필요하다")
        timeout = float(sensor_cfg.get("timeout_seconds", 5.0))
        return SensorDataProvider(HttpSensorSource(url, timeout_seconds=timeout))
    if kind == "spring":
        url = sensor_cfg.get("url")
        if not url:
            raise ValueError("sensor.source=spring에는 sensor.url이 필요하다")
        token_env = sensor_cfg.get("token_env")
        if not token_env:
            raise ValueError("sensor.source=spring에는 sensor.token_env가 필요하다")
        token_header = str(sensor_cfg.get("token_header", "Authorization"))
        timeout = float(sensor_cfg.get("timeout_seconds", 5.0))

        def _token_provider(env_name: str = str(token_env)) -> str:
            token = os.environ.get(env_name, "").strip()
            if not token:
                raise RuntimeError(f"{env_name} 환경변수가 비어 있다 (sensor.source=spring)")
            return token

        return SensorDataProvider(
            SpringSensorSource(
                url,
                token_provider=_token_provider,
                token_header=token_header,
                timeout_seconds=timeout,
            )
        )
    raise ValueError(f"알 수 없는 sensor.source: {kind}")
