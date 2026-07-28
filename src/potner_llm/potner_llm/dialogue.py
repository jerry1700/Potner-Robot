from __future__ import annotations

from typing import Any, Callable, Optional

from .client import ChatMessage, LlmClient, create_llm_client
from .events import EventStore
from .prompts import briefing_user_prompt, report_user_prompt
from .status import PlantStatus
from .templates import render_briefing, render_report
from .tools import ToolHub

StatusProvider = Callable[[], PlantStatus]


class DialogueService:
    """상태/이벤트 공급자를 주입받아 LLM 브리핑·리포트·채팅을 수행한다.

    Raspberry sensor-collector의 PlantService에서 센서/음성/전송 의존을 제거한 버전.
    ROS 노드나 미션 매니저에서 status/event_store만 넘기면 된다.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        get_status: StatusProvider,
        event_store: EventStore,
    ) -> None:
        self.config = config
        self._get_status = get_status
        self.event_store = event_store
        self.llm: LlmClient = create_llm_client(config)

    def brief(self) -> str:
        status = self._get_status()
        text: Optional[str] = None
        if self.llm.available():
            text = self.llm.complete(briefing_user_prompt(status.to_prompt_dict()))
        return text or render_briefing(status)

    def report(self, *, limit: int = 40) -> str:
        status = self._get_status()
        events = self.event_store.recent(limit=limit)
        text: Optional[str] = None
        if self.llm.available():
            text = self.llm.complete(
                report_user_prompt(events, status.to_prompt_dict())
            )
        return text or render_report(events, status)

    def chat_once(self, user_text: str) -> str:
        status = self._get_status()
        tools = ToolHub(get_status=self._get_status, event_store=self.event_store)

        if self.llm.available():
            history = [ChatMessage(role="user", content=user_text)]
            try:
                return self.llm.chat_with_tools(history, tools)
            except RuntimeError:
                pass

        return (
            f"지금은 로컬 모드야. 현재 상태는 {status.summary_ko}. "
            f"(토양 {status.soil.label_ko} / 온도 {status.temperature.label_ko} / "
            f"습도 {status.humidity.label_ko} / 조도 {status.light.label_ko})"
        )
