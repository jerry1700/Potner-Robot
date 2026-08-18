"""대화 컨텍스트 조립(context_builder) + DialogueService 연동 단위 테스트.

실제 네트워크는 절대 타지 않는다 — LLM 호출은 chat_with_tools를 가짜로
바꿔치기하고, HTTP 레벨을 검증할 땐 test_llm.py와 같은 urlopen 몽키패치
패턴만 쓴다.
"""

from __future__ import annotations


from potner_llm.client import ChatMessage
from potner_llm.context_builder import (
    ContextBuilder,
    estimate_tokens,
    message_tokens,
    trim_history,
)
from potner_llm.dialogue import DialogueService
from potner_llm.events import EventStore
from potner_llm.status import MetricLevel, PlantStatus


def _sample_status(*, needs_attention: bool = False) -> PlantStatus:
    level = "low" if needs_attention else "normal"
    label = "건조" if needs_attention else "적정"
    metric = MetricLevel(
        name="soil", value=20.0 if needs_attention else 50.0, level=level, label_ko=label
    )
    ok = MetricLevel(name="ok", value=25.0, level="normal", label_ko="적정")
    return PlantStatus(
        timestamp="2026-07-31T00:00:00",
        soil=metric,
        temperature=ok,
        humidity=ok,
        light=ok,
        summary_ko="토양이 건조해요" if needs_attention else "전반적으로 양호해요",
        needs_attention=needs_attention,
    )


def _user(text: str) -> ChatMessage:
    return ChatMessage(role="user", content=text)


def _assistant(text: str) -> ChatMessage:
    return ChatMessage(role="assistant", content=text)


def _tool_call_pair(question: str, answer: str) -> list[ChatMessage]:
    """user -> assistant(tool_calls) -> tool -> assistant 로 이어지는 한 교환."""
    return [
        _user(question),
        ChatMessage(
            role="assistant",
            content=None,
            tool_calls=[
                {
                    "id": "call-1",
                    "type": "function",
                    "function": {"name": "get_plant_status", "arguments": "{}"},
                }
            ],
        ),
        ChatMessage(role="tool", content='{"주의필요": false}', tool_call_id="call-1"),
        _assistant(answer),
    ]


# --- 토큰 근사 ---


def test_estimate_tokens_counts_korean_conservatively():
    assert estimate_tokens("") == 0
    assert estimate_tokens(None) == 0
    # 한글은 자당 1토큰으로 보수적으로 - ASCII 4자당 1토큰보다 크게 잡힌다.
    assert estimate_tokens("가나다라") == 4
    assert estimate_tokens("abcdefgh") == 2
    assert estimate_tokens("a") == 1  # 최소 1


def test_message_tokens_includes_tool_calls_payload():
    plain = _assistant("답변")
    with_tools = _tool_call_pair("질문", "답변")[1]
    assert message_tokens(with_tools) > message_tokens(plain)


# --- trim_history ---


def test_trim_history_empty_and_unlimited():
    assert trim_history([]) == []
    history = [_user("q1"), _assistant("a1"), _user("q2"), _assistant("a2")]
    assert trim_history(history) == history  # 제한 없음(0)이면 전체 유지


def test_trim_history_cuts_at_exchange_boundary():
    history = [_user("첫번째"), _assistant("r1"), _user("두번째"), _assistant("r2")]
    trimmed = trim_history(history, max_messages=2)
    assert [m.content for m in trimmed] == ["두번째", "r2"]


def test_trim_history_never_splits_tool_call_pair():
    history = _tool_call_pair("옛날 질문", "옛날 답") + _tool_call_pair("최근 질문", "최근 답")
    # 옛 방식(-3 슬라이스)이라면 tool 응답부터 시작하는 비정합 시퀀스가 나온다.
    trimmed = trim_history(history, max_messages=3)
    assert trimmed[0].role == "user"
    assert trimmed[0].content == "최근 질문"
    # assistant(tool_calls) 뒤에 tool 응답이 반드시 붙어 있어야 한다.
    for i, message in enumerate(trimmed):
        if message.tool_calls:
            assert trimmed[i + 1].role == "tool"
            assert trimmed[i + 1].tool_call_id == "call-1"


def test_trim_history_keeps_latest_exchange_even_over_budget():
    history = _tool_call_pair("질문", "답")  # 4개 메시지 - max_messages=2보다 크다
    trimmed = trim_history(history, max_messages=2)
    # 예산을 넘어도 가장 최근 교환은 통째로 남는다 (직전 맥락 상실 방지).
    assert len(trimmed) == 4
    assert trimmed[0].content == "질문"


def test_trim_history_token_budget_drops_old_long_exchanges():
    long_text = "가" * 500  # 근사 500토큰
    history = [
        _user(long_text),
        _assistant(long_text),
        _user("최근 질문"),
        _assistant("최근 답"),
    ]
    trimmed = trim_history(history, max_tokens=100)
    assert [m.content for m in trimmed] == ["최근 질문", "최근 답"]


def test_trim_history_leading_orphan_block_dropped_whole():
    # 백엔드 복원이 어긋나 user 없이 assistant부터 시작하는 경우 - 선두
    # 블록은 통째로 버려지거나 통째로 남지, 중간에서 잘리지 않는다.
    history = [_assistant("고아 답변"), _user("질문"), _assistant("답")]
    trimmed = trim_history(history, max_messages=2)
    assert [m.content for m in trimmed] == ["질문", "답"]


# --- ContextBuilder ---


def test_build_puts_status_and_profile_in_system_prompt_only():
    builder = ContextBuilder()
    context = builder.build(
        history=[],
        user_text="목말라?",
        status_dict={"요약": "전반적으로 양호해요"},
        user_profile={"이름": "동규", "호칭": "주인님"},
    )
    assert "전반적으로 양호해요" in context.system_prompt
    assert "동규" in context.system_prompt
    assert "주인님" in context.system_prompt
    # 사용자 발화는 시스템 프롬프트에 섞이지 않는다 (주입 방지 불변식).
    assert "목말라?" not in context.system_prompt
    assert context.messages[-1].role == "user"
    assert context.messages[-1].content == "목말라?"


def test_build_includes_interaction_notes():
    builder = ContextBuilder()
    context = builder.build(
        history=[],
        user_text="안녕",
        interaction_notes=["어제 물을 줬다", "아침마다 인사한다"],
    )
    assert "어제 물을 줬다" in context.system_prompt
    assert "아침마다 인사한다" in context.system_prompt


def test_build_notes_dropped_history_in_system_prompt():
    builder = ContextBuilder(max_history_turns=1)
    history = [_user("옛 질문"), _assistant("옛 답"), _user("최근 질문"), _assistant("최근 답")]
    context = builder.build(history=history, user_text="이어지는 질문")
    assert context.dropped_messages == 2
    assert "생략" in context.system_prompt
    # 남은 히스토리 + 이번 발화 순서가 유지된다.
    assert [m.content for m in context.messages] == ["최근 질문", "최근 답", "이어지는 질문"]


def test_build_without_drop_has_no_memo_section():
    builder = ContextBuilder()
    context = builder.build(history=[_user("q"), _assistant("a")], user_text="다음")
    assert context.dropped_messages == 0
    assert "[대화 메모]" not in context.system_prompt


def test_build_token_budget_shrinks_history_but_keeps_latest():
    builder = ContextBuilder(max_history_turns=50, max_context_tokens=300)
    long_text = "나" * 400
    history = [
        _user(long_text),
        _assistant(long_text),
        _user("직전 질문"),
        _assistant("직전 답"),
    ]
    context = builder.build(history=history, user_text="지금 질문")
    contents = [m.content for m in context.messages]
    assert contents == ["직전 질문", "직전 답", "지금 질문"]
    assert context.dropped_messages == 2


# --- DialogueService 연동 ---


def _make_service(tmp_path, **kwargs) -> DialogueService:
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "gms"}},
        get_status=_sample_status,
        event_store=store,
        **kwargs,
    )
    service.llm.api_key = "fake-key"
    return service


def test_chat_once_passes_status_context_as_system_prompt(tmp_path):
    service = _make_service(tmp_path, user_profile={"이름": "동규"})
    captured = {}

    def fake_chat_with_tools(history, tools, **kwargs):
        captured["history"] = list(history)
        captured["system"] = kwargs.get("system")
        return "응답"

    service.llm.chat_with_tools = fake_chat_with_tools

    reply = service.chat_once("나 왔어")

    assert reply == "응답"
    assert captured["system"] is not None
    assert "전반적으로 양호해요" in captured["system"]  # 식물 상태 요약
    assert "동규" in captured["system"]  # 사용자 정보
    assert "나 왔어" not in captured["system"]  # 사용자 발화는 user 메시지로만
    assert [m.content for m in captured["history"]] == ["나 왔어"]


def test_chat_once_multiturn_context_is_maintained(tmp_path):
    """시나리오: 이름을 알려준 뒤 이어지는 질문에서 그 대화가 컨텍스트에 남는다."""
    service = _make_service(tmp_path)
    seen = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen.append([m.content for m in history])
        return f"reply-{len(seen)}"

    service.llm.chat_with_tools = fake_chat_with_tools

    service.chat_once("내 이름은 동규야")
    service.chat_once("내 이름 기억해?")
    service.chat_once("그럼 물은 언제 줄까?")

    assert seen[2] == [
        "내 이름은 동규야",
        "reply-1",
        "내 이름 기억해?",
        "reply-2",
        "그럼 물은 언제 줄까?",
    ]


def test_chat_once_trims_by_token_budget_but_notes_the_past(tmp_path):
    """시나리오: 장문 대화가 쌓여 예산을 넘으면 오래된 턴은 빠지되,
    '이전 대화가 있었다'는 메모가 시스템 프롬프트에 남는다."""
    service = _make_service(tmp_path, max_context_tokens=700)
    captured = {}

    def fake_chat_with_tools(history, tools, **kwargs):
        captured["history"] = [m.content for m in history]
        captured["system"] = kwargs.get("system")
        return "짧은 답"

    service.llm.chat_with_tools = fake_chat_with_tools

    long_text = "다" * 600
    service.chat_once(long_text)
    service.chat_once("방금 질문")
    service.chat_once("이어지는 질문")

    assert long_text not in captured["history"]
    assert captured["history"][-1] == "이어지는 질문"
    assert "생략" in captured["system"]
    # 전체 기록 자체는 손실 없이 보존된다 (저장/복원용).
    assert len(service.history) == 6


def test_chat_once_turn_cap_uses_exchange_boundaries(tmp_path):
    service = _make_service(tmp_path, max_history_turns=1)
    seen = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen.append([m.content for m in history])
        return f"reply-{len(seen)}"

    service.llm.chat_with_tools = fake_chat_with_tools

    service.chat_once("첫번째")
    service.chat_once("두번째")
    service.chat_once("세번째")

    # 기존 계약 유지: max_history_turns=1이면 직전 1턴 + 이번 발화만.
    assert seen[-1] == ["두번째", "reply-2", "세번째"]


def test_chat_once_offline_fallback_still_remembers_turn(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "mock"}},
        get_status=_sample_status,
        event_store=store,
        user_profile={"이름": "동규"},
    )
    reply = service.chat_once("지금 어때?")
    assert "로컬 모드" in reply
    assert [m.content for m in service.history] == ["지금 어때?", reply]


def test_chat_once_does_not_split_restored_tool_pairs(tmp_path):
    """복원/누적된 히스토리에 툴콜 쌍이 있어도 절단면이 쌍 안으로 안 들어간다.

    실제 LLM 호출 경로(chat_once → ContextBuilder.build)로 검증한다 —
    잘려서 tool 메시지가 고아로 남으면 API에 비정합 시퀀스가 넘어간다.
    """
    service = _make_service(tmp_path, max_history_turns=1)
    service._history = _tool_call_pair("옛 질문", "옛 답") + [_user("새 질문"), _assistant("새 답")]
    seen = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen.append(list(history))
        return "이번 답"

    service.llm.chat_with_tools = fake_chat_with_tools
    service.chat_once("이번 질문")

    sent = seen[-1]
    # 툴콜 쌍이 있는 옛 교환은 통째로 잘리고, 최근 1턴 + 이번 발화만 남는다.
    assert [m.content for m in sent] == ["새 질문", "새 답", "이번 질문"]
    assert all(m.role != "tool" for m in sent)


def test_llm_error_still_triggers_offline_fallback(tmp_path):
    """LlmError(RuntimeError 상속)가 나면 컨텍스트 조립과 무관하게 폴백한다."""
    from potner_llm.exceptions import LlmTimeoutError

    service = _make_service(tmp_path)

    def fake_chat_with_tools(history, tools, **kwargs):
        raise LlmTimeoutError("timeout")

    service.llm.chat_with_tools = fake_chat_with_tools

    reply = service.chat_once("있니?")
    assert "로컬 모드" in reply
    assert [m.content for m in service.history] == ["있니?", reply]
