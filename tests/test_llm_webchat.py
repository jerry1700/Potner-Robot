"""웹 챗 LLM 어댑터(voice-chat-server/webchat_llm.py) 단위 테스트.

네트워크는 실호출하지 않는다 — webchat_llm 모듈의 `requests` 속성을
가짜 모듈로 monkeypatch 해서 SSE 스트림·요약·헬스체크를 전부 스텁으로 돌린다.

검증 축:
- `/api/chat` SSE 파싱(text_delta / message_complete / error)
- Origin 헤더를 절대 붙이지 않는다는 규약 (붙이면 웹 챗이 403)
- 세션 히스토리 축적·재전송, 20턴(40메시지) 초과 시 `/api/summarize` 압축
- 실패 시 예외 대신 폴백 문구를 돌려주는 회복 탄력성
"""

from __future__ import annotations

import copy
import json
import sys
from pathlib import Path
from typing import Optional, Union

import pytest
import requests as real_requests

# voice-chat-server 는 colcon 패키지가 아니라 conftest 가 올려주지 않습니다.
SERVER_DIR = Path(__file__).resolve().parent.parent / "voice-chat-server"
if str(SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(SERVER_DIR))

import webchat_llm  # noqa: E402
from webchat_llm import (  # noqa: E402
    MAX_HISTORY_MESSAGES,
    MSG_LLM_ERROR,
    MSG_UNREACHABLE,
    WebChatLLM,
)

BASE_URL = "http://testserver:3000"


# --- 스텁: requests 대역 ---


class _FakeResponse:
    """requests.Response 대역 — SSE 라인 스트림 또는 JSON 바디를 흉내낸다."""

    def __init__(
        self,
        lines: Optional[list[str]] = None,
        status_code: int = 200,
        json_body: Optional[dict] = None,
        text: str = "",
    ) -> None:
        self.status_code = status_code
        self._lines = lines or []
        self._json = json_body
        self.text = text
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines

    def close(self) -> None:
        """stream=True 응답은 어댑터가 반드시 닫아야 한다 — 커넥션 누수 검증용."""
        self.closed = True

    def json(self):
        if self._json is None:
            raise ValueError("no json body")
        return self._json


class _FakeRequests:
    """webchat_llm.requests 자리에 꽂는 가짜 모듈.

    except 절이 실제 requests 예외 타입을 참조하므로 exceptions 는 진짜를 쓴다.
    """

    exceptions = real_requests.exceptions

    def __init__(self) -> None:
        # URL suffix → 응답(또는 예외) 큐. 비면 마지막 항목을 재사용한다.
        self.chat_queue: list[Union[_FakeResponse, Exception]] = []
        self.summarize_result: Union[_FakeResponse, Exception, None] = None
        self.get_error: Optional[Exception] = None
        self.post_calls: list[tuple[str, dict]] = []
        self.get_calls: list[str] = []

    def post(self, url: str, **kwargs) -> _FakeResponse:
        # 진짜 requests.post 는 호출 시점에 json 을 직렬화한다. 어댑터가
        # 호출 후 history 리스트를 이어서 mutate 하므로, 그 시점의 스냅샷을
        # 남기려면 깊은 복사가 필요하다.
        self.post_calls.append((url, copy.deepcopy(kwargs)))
        if url.endswith("/api/chat"):
            result = self.chat_queue.pop(0) if len(self.chat_queue) > 1 else self.chat_queue[0]
        elif url.endswith("/api/summarize"):
            result = self.summarize_result
        else:  # pragma: no cover — 계약에 없는 URL 은 테스트 실패로 드러낸다
            raise AssertionError(f"unexpected POST url: {url}")
        if isinstance(result, Exception):
            raise result
        assert result is not None, f"no stub response for {url}"
        return result

    def get(self, url: str, **kwargs) -> _FakeResponse:
        self.get_calls.append(url)
        if self.get_error is not None:
            raise self.get_error
        return _FakeResponse(status_code=200)

    # -- 조회 헬퍼 --

    def calls_to(self, suffix: str) -> list[tuple[str, dict]]:
        return [(url, kw) for url, kw in self.post_calls if url.endswith(suffix)]


def _sse(*events: tuple[str, dict]) -> list[str]:
    """(event, data) 쌍들을 웹 챗 route.ts 가 보내는 SSE 라인 시퀀스로 변환."""
    lines: list[str] = []
    for name, data in events:
        lines.append(f"event: {name}")
        lines.append("data: " + json.dumps(data, ensure_ascii=False))
        lines.append("")  # 프레임 경계
    return lines


def _deltas(*texts: str) -> list[str]:
    events = [("text_delta", {"text": t}) for t in texts]
    events.append(("message_complete", {}))
    return _sse(*events)


@pytest.fixture
def fake_net(monkeypatch) -> _FakeRequests:
    fake = _FakeRequests()
    monkeypatch.setattr(webchat_llm, "requests", fake)
    return fake


def _llm() -> WebChatLLM:
    return WebChatLLM(base_url=BASE_URL)


# --- 1. SSE 스트림 파싱 ---


def test_chat_stream_yields_text_deltas_in_order(fake_net):
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("안녕", ", ", "초록이야"))]
    llm = _llm()
    assert list(llm.chat_stream("s1", "안녕?")) == ["안녕", ", ", "초록이야"]


def test_chat_once_concatenates_deltas(fake_net):
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("물을 ", "주세요"))]
    assert _llm().chat_once("s1", "물 줘야 해?") == "물을 주세요"


def test_message_complete_stops_reading_stream(fake_net):
    """message_complete 이후의 프레임은 읽지 않는다."""
    lines = _sse(
        ("text_delta", {"text": "끝"}),
        ("message_complete", {}),
        ("text_delta", {"text": "유령 델타"}),
    )
    fake_net.chat_queue = [_FakeResponse(lines=lines)]
    assert _llm().chat_once("s1", "안녕") == "끝"


def test_unknown_events_and_bad_json_are_ignored(fake_net):
    """tool_use 같은 미지 이벤트·깨진 JSON·빈 텍스트는 조용히 건너뛴다."""
    lines = (
        _sse(("tool_use", {"name": "get_sensors"}))
        + ["event: text_delta", "data: {깨진 json", ""]
        + _sse(("text_delta", {"text": ""}))  # 빈 텍스트는 yield 안 함
        + _deltas("진짜 응답")
    )
    fake_net.chat_queue = [_FakeResponse(lines=lines)]
    assert _llm().chat_once("s1", "안녕") == "진짜 응답"


# --- 2. 요청 규약: Origin 금지 · 페이로드 형태 ---


def test_no_origin_header_is_sent(fake_net):
    """웹 챗 isAllowedOrigin() 규약 — Origin 을 붙이면 403 이므로 절대 금지."""
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    _llm().chat_once("s1", "안녕")
    for url, kwargs in fake_net.post_calls:
        headers = kwargs.get("headers") or {}
        assert "Origin" not in headers, f"{url} 요청에 Origin 헤더가 붙었다"


def test_chat_payload_shape(fake_net):
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    _llm().chat_once("s1", "안녕")
    (_, kwargs), = fake_net.calls_to("/api/chat")
    payload = kwargs["json"]
    assert payload["message"] == "안녕"
    assert payload["history"] == []  # 첫 턴은 빈 히스토리
    assert payload["conversationId"]
    assert "summary" not in payload  # 요약 전에는 키 자체가 없어야 한다
    assert kwargs["stream"] is True


def test_base_url_trailing_slash_is_stripped(fake_net):
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    WebChatLLM(base_url=BASE_URL + "/").chat_once("s1", "안녕")
    url, _ = fake_net.post_calls[0]
    assert url == f"{BASE_URL}/api/chat"


# --- 3. 히스토리 관리 ---


def test_history_records_user_and_assistant_after_turn(fake_net):
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("반가워"))]
    llm = _llm()
    llm.chat_once("s1", "안녕")
    assert llm._session("s1").history == [
        {"role": "user", "content": "안녕"},
        {"role": "assistant", "content": "반가워"},
    ]


def test_history_is_sent_on_next_call(fake_net):
    fake_net.chat_queue = [
        _FakeResponse(lines=_deltas("첫 응답")),
        _FakeResponse(lines=_deltas("둘째 응답")),
    ]
    llm = _llm()
    llm.chat_once("s1", "첫 질문")
    llm.chat_once("s1", "둘째 질문")
    (_, first), (_, second) = fake_net.calls_to("/api/chat")
    assert first["json"]["history"] == []
    assert second["json"]["history"] == [
        {"role": "user", "content": "첫 질문"},
        {"role": "assistant", "content": "첫 응답"},
    ]
    # conversationId 는 세션 내에서 유지된다
    assert first["json"]["conversationId"] == second["json"]["conversationId"]


def test_sessions_are_isolated(fake_net):
    fake_net.chat_queue = [
        _FakeResponse(lines=_deltas("A 응답")),
        _FakeResponse(lines=_deltas("B 응답")),
    ]
    llm = _llm()
    llm.chat_once("voice", "질문")
    llm.chat_once("monitor", "질문")
    (_, first), (_, second) = fake_net.calls_to("/api/chat")
    assert second["json"]["history"] == []  # 다른 세션이니 히스토리 공유 금지
    assert first["json"]["conversationId"] != second["json"]["conversationId"]


# --- 4. 요약(summarize) ---


def _turns(n: int) -> list[dict[str, str]]:
    """user/assistant 를 번갈아 n 개 메시지 생성."""
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
        for i in range(n)
    ]


def test_summarize_triggers_when_history_exceeds_limit(fake_net):
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    fake_net.summarize_result = _FakeResponse(json_body={"summary": "지난 이야기 요약"})
    llm = _llm()
    session = llm._session("s1")
    session.history = _turns(MAX_HISTORY_MESSAGES + 2)

    llm.chat_once("s1", "새 질문")

    (_, sum_kwargs), = fake_net.calls_to("/api/summarize")
    # 오래된 2개만 요약으로 접힌다
    assert sum_kwargs["json"]["messages"] == _turns(MAX_HISTORY_MESSAGES + 2)[:2]
    assert sum_kwargs["json"]["previousSummary"] == ""
    # chat 페이로드에는 압축된 40개 히스토리 + 새 summary 가 실린다
    (_, chat_kwargs), = fake_net.calls_to("/api/chat")
    assert len(chat_kwargs["json"]["history"]) == MAX_HISTORY_MESSAGES
    assert chat_kwargs["json"]["history"][0] == {"role": "user", "content": "m2"}
    assert chat_kwargs["json"]["summary"] == "지난 이야기 요약"
    assert session.summary == "지난 이야기 요약"


def test_summarize_not_triggered_at_exact_limit(fake_net):
    """정확히 40개(경계값)에서는 요약하지 않는다."""
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    llm = _llm()
    llm._session("s1").history = _turns(MAX_HISTORY_MESSAGES)
    llm.chat_once("s1", "질문")
    assert fake_net.calls_to("/api/summarize") == []


def test_summarize_failure_keeps_history_for_retry(fake_net):
    """요약 실패 시 히스토리를 자르지 않는다 — 잘라버리면 옛 턴이 요약도
    없이 유실된다. 자르지 않고 두면 다음 턴에 다시 시도할 수 있고, 웹 챗
    서버가 어차피 최근 20턴만 쓰므로 길게 보내도 무해하다."""
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    fake_net.summarize_result = real_requests.exceptions.Timeout("느림")
    llm = _llm()
    llm._session("s1").history = _turns(MAX_HISTORY_MESSAGES + 2)

    reply = llm.chat_once("s1", "질문")

    assert reply == "응답"  # 대화는 계속된다
    (_, chat_kwargs), = fake_net.calls_to("/api/chat")
    # 자르지 않았으므로 42개가 그대로 실려 나간다 (서버가 알아서 최근분만 씀).
    assert len(chat_kwargs["json"]["history"]) == MAX_HISTORY_MESSAGES + 2
    assert "summary" not in chat_kwargs["json"]
    # 이번 턴이 붙어 44개 — 다음 턴의 _maybe_summarize가 다시 시도한다.
    assert len(llm._session("s1").history) == MAX_HISTORY_MESSAGES + 4


def test_empty_summary_also_keeps_history(fake_net):
    """서버가 빈 요약을 돌려줘도 자르지 않는다 — 빈 요약으로 자르면 유실이다."""
    fake_net.chat_queue = [_FakeResponse(lines=_deltas("응답"))]
    fake_net.summarize_result = _FakeResponse(json_body={"summary": ""})
    llm = _llm()
    llm._session("s1").history = _turns(MAX_HISTORY_MESSAGES + 2)

    llm.chat_once("s1", "질문")

    (_, chat_kwargs), = fake_net.calls_to("/api/chat")
    assert len(chat_kwargs["json"]["history"]) == MAX_HISTORY_MESSAGES + 2


# --- 스트림 응답 정리 (커넥션 누수 방지) ---


def test_stream_response_closed_after_normal_exhaustion(fake_net):
    response = _FakeResponse(lines=_deltas("전부", "소진"))
    fake_net.chat_queue = [response]

    _llm().chat_once("s1", "질문")

    assert response.closed


def test_stream_response_closed_on_message_complete_early_return(fake_net):
    """message_complete 조기 반환 경로 — return으로 빠져도 닫혀야 한다."""
    response = _FakeResponse(
        lines=[
            "event: text_delta",
            'data: {"text": "부분"}',
            "",
            "event: message_complete",
            "data: {}",
            "",
            "event: text_delta",
            'data: {"text": "여기는 안 읽음"}',
        ]
    )
    fake_net.chat_queue = [response]

    reply = _llm().chat_once("s1", "질문")

    assert reply == "부분"
    assert response.closed


def test_stream_response_closed_on_non_200(fake_net):
    response = _FakeResponse(status_code=500, json_body={"message": "서버 오류"})
    fake_net.chat_queue = [response]

    _llm().chat_once("s1", "질문")  # 폴백 문구로 계속되지만

    assert response.closed  # 응답은 닫혀 있어야 한다


# --- 5. 세션 종료 · 헬스체크 ---


def test_end_session_clears_state(fake_net):
    fake_net.chat_queue = [
        _FakeResponse(lines=_deltas("응답")),
        _FakeResponse(lines=_deltas("응답2")),
    ]
    llm = _llm()
    llm.chat_once("s1", "질문")
    assert llm.end_session("s1") is True
    assert llm.end_session("s1") is False  # 이미 없으면 False
    llm.chat_once("s1", "새 질문")
    (_, _), (_, second) = fake_net.calls_to("/api/chat")
    assert second["json"]["history"] == []  # 종료 후엔 히스토리 초기화


def test_healthy_true_when_server_responds(fake_net):
    assert _llm().healthy() is True
    assert fake_net.get_calls == [BASE_URL]


def test_healthy_false_when_requests_raises(fake_net):
    fake_net.get_error = real_requests.exceptions.ConnectionError("죽음")
    assert _llm().healthy() is False


# --- 6. 오류 회복 탄력성 ---


def test_error_sse_event_yields_llm_error_fallback(fake_net):
    """error 이벤트 → 예외 대신 폴백 문구. 서버 자체는 살아 있으니 MSG_LLM_ERROR."""
    fake_net.chat_queue = [
        _FakeResponse(lines=_sse(("error", {"message": "overloaded"})))
    ]
    llm = _llm()
    assert llm.chat_once("s1", "질문") == MSG_LLM_ERROR
    assert llm._session("s1").history == []  # 폴백 턴은 히스토리에 남기지 않는다


def test_connection_error_yields_unreachable_fallback(fake_net):
    """서버가 아예 죽어 있으면(healthy 도 실패) MSG_UNREACHABLE."""
    fake_net.chat_queue = [real_requests.exceptions.ConnectionError("연결 거부")]
    fake_net.get_error = real_requests.exceptions.ConnectionError("연결 거부")
    llm = _llm()
    assert llm.chat_once("s1", "질문") == MSG_UNREACHABLE
    assert llm._session("s1").history == []


def test_non_200_response_yields_fallback(fake_net):
    fake_net.chat_queue = [
        _FakeResponse(status_code=500, json_body={"message": "boom"})
    ]
    assert _llm().chat_once("s1", "질문") == MSG_LLM_ERROR


def test_partial_stream_then_error_keeps_partial_reply(fake_net):
    """부분 성공 문서화: 델타 일부를 받고 error 가 나면 그 부분을 응답으로 확정."""
    lines = _sse(
        ("text_delta", {"text": "반쯤 "}),
        ("text_delta", {"text": "말하다"}),
        ("error", {"message": "stream cut"}),
    )
    fake_net.chat_queue = [_FakeResponse(lines=lines)]
    llm = _llm()
    assert list(llm.chat_stream("s1", "질문")) == ["반쯤 ", "말하다"]
    # 부분 응답도 히스토리에 기록된다 (docstring 의 '부분 성공 포함')
    assert llm._session("s1").history[-1] == {
        "role": "assistant",
        "content": "반쯤 말하다",
    }
