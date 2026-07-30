from __future__ import annotations

from typing import Any, Callable

from .events import EventStore
from .status import PlantStatus


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
]


class ToolHub:
    def __init__(
        self,
        *,
        get_status: Callable[[], PlantStatus],
        event_store: EventStore,
    ) -> None:
        self._get_status = get_status
        self._event_store = event_store

    def call(self, name: str, arguments: dict[str, Any] | None = None) -> Any:
        arguments = arguments or {}
        if name == "get_plant_status":
            return self._get_status().to_prompt_dict()
        if name == "get_recent_events":
            limit = int(arguments.get("limit", 20))
            return self._event_store.recent(limit=limit)
        raise ValueError(f"Unknown tool: {name}")
