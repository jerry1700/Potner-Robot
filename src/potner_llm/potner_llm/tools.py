from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .events import EventStore
from .models import SensorSnapshot
from .sensor_provider import classify_metric
from .status import PlantStatus

logger = logging.getLogger(__name__)

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "get_plant_status",
            "description": "현재 식물의 온습도/토양수분/조도 상태 요약을 가져온다.",
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_events",
            "description": "최근 이벤트 로그(상태 변화, 주의 알림 등)를 가져온다.",
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {
                        "type": "integer",
                        "description": "가져올 최대 개수",
                        "default": 20,
                    }
                },
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_sensor_data",
            "description": (
                "개별 센서(토양 수분·온도·습도·조도·CO₂)의 최신 원시 측정값과 "
                "판정 라벨을 가져온다. 값이 null이면 해당 센서 데이터가 없는 것이다."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    # 웹 조회 툴 — description은 "언제 호출해야 하는가"로 쓴다 (Opus 계열이
    # 툴을 보수적으로 고르기 때문. plant-robot-chat lib/tools.ts와 같은 원칙).
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": (
                "사용자가 바깥 날씨·기온·비·눈·예보(오늘/내일/주말)를 물을 때 호출한다. "
                "실내 센서와 다른 실제 바깥 날씨와 3일 예보를 가져온다."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_plant_knowledge",
            "description": (
                "식물 관리법·병충해·품종 특성 등 일반 지식 질문에 확신이 없을 때 호출한다. "
                "한국어 위키백과에서 관련 문서 요약을 가져온다."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "검색어 (예: '스킨답서스', '잎 황화 원인')",
                    }
                },
                "required": ["query"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_news",
            "description": (
                "사용자가 오늘의 뉴스·세상 소식을 물을 때 호출한다. "
                "주요 뉴스 헤드라인 목록을 가져온다."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
    },
]


def anthropic_tool_definitions() -> list[dict[str, Any]]:
    """TOOL_DEFINITIONS(OpenAI function 스키마)를 Anthropic Messages 형식으로 변환한다.

    원본은 OpenAI 형식 하나만 유지한다 — 두 벌을 손으로 관리하면 반드시 어긋난다.
    """
    return [
        {
            "name": t["function"]["name"],
            "description": t["function"]["description"],
            "input_schema": t["function"]["parameters"],
        }
        for t in TOOL_DEFINITIONS
    ]


class ToolHub:
    def __init__(
        self,
        *,
        get_status: Callable[[], PlantStatus],
        event_store: EventStore,
        get_sensors: Optional[Callable[[], Optional[SensorSnapshot]]] = None,
        web: Optional[Any] = None,
    ) -> None:
        self._get_status = get_status
        self._event_store = event_store
        self._get_sensors = get_sensors
        self._web = web  # web_provider.WebProvider (없으면 웹 툴은 비활성 안내)

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        """툴을 실행한다. 결과는 LLM에게 되돌아가므로 절대 예외를 던지지 않는다 —
        chat_with_tools 루프 중간에 예외가 새면 사용자 질문이 통째로 죽는다."""
        arguments = arguments or {}
        try:
            result = self._dispatch(name, arguments)
        except Exception as exc:
            logger.exception(f"툴 실행 실패: {name}({arguments!r})")
            return {"error": f"tool_failed:{name}", "안내": f"{name} 조회에 실패했어요: {exc}"}
        logger.info(f"툴 실행 성공: {name}")
        return result

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> Any:
        if name == "get_plant_status":
            return self._get_status().to_prompt_dict()
        if name == "get_recent_events":
            limit = int(arguments.get("limit", 20))
            return self._event_store.recent(limit=limit)
        if name == "get_sensor_data":
            return self._sensor_data()
        if name == "get_weather":
            if self._web is None:
                return {"error": "web_disabled", "안내": "바깥 날씨 조회 기능이 꺼져 있어요."}
            return self._web.weather()
        if name == "search_plant_knowledge":
            if self._web is None:
                return {"error": "web_disabled", "안내": "검색 기능이 꺼져 있어요."}
            return self._web.search_knowledge(str(arguments.get("query", "")))
        if name == "get_news":
            if self._web is None:
                return {"error": "web_disabled", "안내": "뉴스 조회 기능이 꺼져 있어요."}
            return self._web.news()
        # LLM이 없는 툴 이름을 지어내는 경우가 실제로 있다 — 죽이지 말고 알려준다.
        logger.warning(f"알 수 없는 툴 호출: {name}")
        return {"error": f"unknown_tool:{name}", "사용가능한_툴": [t["function"]["name"] for t in TOOL_DEFINITIONS]}

    def _sensor_data(self) -> dict[str, Any]:
        snapshot = self._get_sensors() if self._get_sensors is not None else None
        if snapshot is None:
            # 센서 provider가 없거나(dialogue 스택 기본) 조회가 비었으면
            # 상태 라벨의 세부값으로라도 답한다 — 빈손보다는 낫다.
            status = self._get_status()
            return {
                "출처": "status",
                "측정값": status.to_prompt_dict().get("세부", {}),
                "안내": "실시간 센서 스냅샷이 없어 상태 요약의 세부값을 사용했어요.",
            }
        pairs = (
            ("토양수분_pct", "soil", snapshot.soil),
            ("온도_c", "temperature", snapshot.temp),
            ("습도_pct", "humidity", snapshot.humidity),
            ("조도_lux", "light", snapshot.light),
        )
        data: dict[str, Any] = {"출처": "sensor", "측정값": {}, "판정": {}}
        for key, metric_name, value in pairs:
            data["측정값"][key] = value
            data["판정"][key] = classify_metric(metric_name, value).label_ko
        data["측정값"]["co2_ppm"] = snapshot.co2
        if snapshot.photo_summary:
            data["최근_촬영"] = snapshot.photo_summary
        return data
