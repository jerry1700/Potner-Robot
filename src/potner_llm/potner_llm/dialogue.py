from __future__ import annotations

from typing import Any, Callable, Optional

from .client import ChatMessage, LlmClient, create_llm_client
from .conversation_backend import ConversationBackend
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

    conversation_backend를 주면, 생성 시점에 지난 대화(같은 session_id)를
    불러와 이어서 기억하고, end_conversation() 호출 시 지금까지의 대화를
    백엔드에 저장한 뒤 맥락을 비운다.
    """

    def __init__(
        self,
        config: dict[str, Any],
        *,
        get_status: StatusProvider,
        event_store: EventStore,
        max_history_turns: int = 10,
        conversation_backend: Optional[ConversationBackend] = None,
        session_id: str = "default",
    ) -> None:
        self.config = config
        self._get_status = get_status
        self.event_store = event_store
        self.llm: LlmClient = create_llm_client(config)
        self._max_history_turns = max_history_turns
        self._backend = conversation_backend
        self._session_id = session_id
        self._history: list[ChatMessage] = (
            self._restore_history() if self._backend is not None else []
        )

    @property
    def history(self) -> list[ChatMessage]:
        return list(self._history)

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
        user_message = ChatMessage(role="user", content=user_text)

        if self.llm.available():
            try:
                context = self._bounded_history() + [user_message]
                reply = self.llm.chat_with_tools(context, tools)
                self._remember_turn(user_message, ChatMessage(role="assistant", content=reply))
                return reply
            except RuntimeError:
                pass

        reply = (
            f"지금은 로컬 모드야. 현재 상태는 {status.summary_ko}. "
            f"(토양 {status.soil.label_ko} / 온도 {status.temperature.label_ko} / "
            f"습도 {status.humidity.label_ko} / 조도 {status.light.label_ko})"
        )
        self._remember_turn(user_message, ChatMessage(role="assistant", content=reply))
        return reply

    def reset_history(self) -> None:
        """대화 맥락만 지운다 (백엔드 저장 없이). 예: 도중에 화제를 리셋할 때."""
        self._history = []

    def end_conversation(self) -> None:
        """한 대화를 마칠 때 호출한다.

        지금까지의 대화 전체를 백엔드로 넘겨 저장하고 맥락을 비운다. 다음에
        같은 session_id로 DialogueService를 새로 만들면 이 대화를 이어서
        기억한다. conversation_backend가 없으면 맥락만 비운다.

        이번 세션에서 오간 대화가 없으면(바로 종료한 경우) 저장을 건너뛴다 —
        그렇지 않으면 빈 목록으로 덮어써서 이전에 저장해 둔 대화가 지워진다.
        """
        if self._backend is not None and self._history:
            payload = [{"role": m.role, "content": m.content} for m in self._history]
            self._backend.save(self._session_id, payload)
        self.reset_history()

    def _restore_history(self) -> list[ChatMessage]:
        """백엔드에 저장된 대화 전체를 그대로 불러온다 (기록은 손실 없이 보존).

        LLM에 매 호출마다 전체를 다시 넘기면 대화가 길어질수록 토큰 비용이
        계속 커지므로, 실제로 프롬프트에 넣는 양은 _bounded_history()에서
        최근 max_history_turns턴만 잘라 쓴다.
        """
        assert self._backend is not None
        raw = self._backend.load(self._session_id)
        return [
            ChatMessage(role=str(item.get("role", "user")), content=item.get("content"))
            for item in raw
            if item.get("content")
        ]

    def _bounded_history(self) -> list[ChatMessage]:
        max_messages = self._max_history_turns * 2
        if max_messages <= 0:
            return list(self._history)
        return self._history[-max_messages:]

    def _remember_turn(self, user_message: ChatMessage, assistant_message: ChatMessage) -> None:
        self._history.append(user_message)
        self._history.append(assistant_message)
