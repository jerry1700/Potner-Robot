from __future__ import annotations

import logging
from typing import Any, Callable, Optional

from .client import ChatMessage, LlmClient, create_llm_client
from .context_builder import DEFAULT_MAX_CONTEXT_TOKENS, ContextBuilder
from .conversation_backend import ConversationBackend
from .events import EventStore
from .factcheck import (
    ACTION_RETRY,
    DEFAULT_POLICY,
    next_action,
    snapshot_from_status,
    verify_response,
)
from .prompts import (
    briefing_user_prompt,
    diary_system_prompt,
    diary_user_prompt,
    report_user_prompt,
)
from .status import PlantStatus
from .templates import render_briefing, render_diary, render_report
from .tools import ToolHub
from .web_provider import create_web_provider

logger = logging.getLogger(__name__)

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
        max_context_tokens: int = DEFAULT_MAX_CONTEXT_TOKENS,
        user_profile: Optional[dict[str, Any]] = None,
        verify_facts: bool = True,
        conversation_backend: Optional[ConversationBackend] = None,
        session_id: str = "default",
    ) -> None:
        self.config = config
        self._get_status = get_status
        self.event_store = event_store
        self.llm: LlmClient = create_llm_client(config)
        self._max_history_turns = max_history_turns
        # 사용자 정보는 아직 공용 모델이 없어 가벼운 dict로 받는다
        # (예: {"이름": "동규", "호칭": "주인님"}). 시스템 프롬프트에만 실린다.
        self._user_profile = user_profile
        self._verify_facts = verify_facts
        self._context_builder = ContextBuilder(
            max_history_turns=max_history_turns,
            max_context_tokens=max_context_tokens,
        )
        self._backend = conversation_backend
        self._session_id = session_id
        # 웹 조회(날씨·지식·뉴스)는 config의 web: 섹션으로 켠다. 여기서 한 번
        # 만들어 두면 호출부(cli.py/app.py) 수정 없이 모든 대화에 적용되고,
        # provider의 최근 성공값 캐시도 대화 간에 유지된다.
        self._web = create_web_provider(config)
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

    def diary(
        self,
        *,
        date: Optional[str] = None,
        limit: int = 40,
        persona: Optional[dict[str, Any]] = None,
        user_activities: Optional[list[Any]] = None,
    ) -> str:
        """오늘 하루를 식물 1인칭 일기로 쓴다.

        상태 + 이벤트 로그(+ 사용자 상호작용 기록)를 근거 자료로 넘기고,
        생성 결과는 chat_once와 같은 사실성 검증을 거친다 — 상태와 상충하는
        일기(지어낸 수치·급수 등)는 버리고 기록 기반 템플릿으로 폴백한다.
        """
        status = self._get_status()
        events = self.event_store.recent(limit=limit)
        text: Optional[str] = None
        if self.llm.available():
            try:
                text = self.llm.complete(
                    diary_user_prompt(
                        status.to_prompt_dict(),
                        events,
                        date=date,
                        user_activities=user_activities,
                    ),
                    system=diary_system_prompt(persona),
                )
                if text and self._verify_facts:
                    fact = verify_response(text, snapshot_from_status(status))
                    if not fact.ok:
                        logger.warning(
                            f"일기 사실성 검증 실패 — 템플릿 폴백 ({', '.join(fact.issue_codes)})"
                        )
                        text = None
            except RuntimeError:
                text = None
        return text or render_diary(status, events, date=date)

    def chat_once(self, user_text: str) -> str:
        status = self._get_status()
        tools = ToolHub(
            get_status=self._get_status, event_store=self.event_store, web=self._web
        )
        user_message = ChatMessage(role="user", content=user_text)

        if self.llm.available():
            try:
                # 상태 요약/사용자 정보는 시스템 프롬프트로, 사용자 발화는
                # 별도 user 메시지로 유지한다 (프롬프트 주입 방지 불변식).
                context = self._context_builder.build(
                    history=self._history,
                    user_text=user_text,
                    status_dict=status.to_prompt_dict(),
                    user_profile=self._user_profile,
                )
                fact = None
                for attempt in range(DEFAULT_POLICY.max_retries + 1):
                    reply = self.llm.chat_with_tools(
                        context.messages, tools, system=context.system_prompt
                    )
                    if not self._verify_facts:
                        break
                    # 사실성 검증 — 상태와 상충하는 답이면 1회 재생성 후 폴백
                    fact = verify_response(reply, snapshot_from_status(status))
                    if next_action(fact, attempt) != ACTION_RETRY:
                        break
                if fact is not None and not fact.ok:
                    logger.warning(
                        f"chat_once 사실성 검증 실패 — 상태 기반 폴백으로 대체 "
                        f"({', '.join(fact.issue_codes)})"
                    )
                    reply = f"음, 방금은 말이 좀 꼬였나 봐. {self._grounded_reply(status)}"
                self._remember_turn(user_message, ChatMessage(role="assistant", content=reply))
                return reply
            except RuntimeError:
                pass

        reply = f"지금은 로컬 모드야. {self._grounded_reply(status)}"
        self._remember_turn(user_message, ChatMessage(role="assistant", content=reply))
        return reply

    def _grounded_reply(self, status: PlantStatus) -> str:
        """센서 상태만으로 만드는 안전한 답 — 오프라인/검증 실패 폴백 공용."""
        return (
            f"현재 상태는 {status.summary_ko}. "
            f"(토양 {status.soil.label_ko} / 온도 {status.temperature.label_ko} / "
            f"습도 {status.humidity.label_ko} / 조도 {status.light.label_ko})"
        )

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
        계속 커지므로, 실제로 프롬프트에 넣는 양은 ContextBuilder.build()가
        최근 max_history_turns턴·토큰 예산 안으로 잘라 쓴다.
        """
        assert self._backend is not None
        raw = self._backend.load(self._session_id)
        return [
            ChatMessage(role=str(item.get("role", "user")), content=item.get("content"))
            for item in raw
            if item.get("content")
        ]

    def _remember_turn(self, user_message: ChatMessage, assistant_message: ChatMessage) -> None:
        self._history.append(user_message)
        self._history.append(assistant_message)
