"""AnthropicLlmClient 단위 테스트 (실 GMS 호출 없음 — requests.post 전부 대역).

GMS Anthropic 프록시의 실측 제약을 계약으로 검증한다:
스트리밍 필수 / temperature 미전송 / x-api-key 인증.
"""

from __future__ import annotations

import json

import pytest
import requests

from potner_llm import client as client_module
from potner_llm.client import (
    AnthropicLlmClient,
    ChatMessage,
    LlmClient,
    create_llm_client,
)
from potner_llm.exceptions import (
    LlmApiError,
    LlmAuthError,
    LlmConnectionError,
    LlmRateLimitError,
    LlmTimeoutError,
)
from potner_llm.tools import anthropic_tool_definitions


# --- SSE 대역 ---


def _sse(events: list[tuple[str, dict]]) -> list[str]:
    lines: list[str] = []
    for name, payload in events:
        lines.append(f"event: {name}")
        lines.append("data: " + json.dumps(payload, ensure_ascii=False))
        lines.append("")
    return lines


def _text_stream(text: str, *, stop_reason: str = "end_turn") -> list[str]:
    return _sse(
        [
            ("message_start", {"message": {"usage": {"input_tokens": 10}}}),
            ("content_block_start", {"content_block": {"type": "text"}}),
            ("content_block_delta", {"delta": {"type": "text_delta", "text": text}}),
            ("content_block_stop", {}),
            ("message_delta", {"delta": {"stop_reason": stop_reason}, "usage": {"output_tokens": 5}}),
            ("message_stop", {}),
        ]
    )


def _tool_use_stream(tool_id: str, name: str, args_json: str) -> list[str]:
    # input_json_delta가 여러 조각으로 쪼개져 오는 실제 스트림 형태를 흉내낸다.
    return _sse(
        [
            ("message_start", {"message": {}}),
            ("content_block_start", {"content_block": {"type": "text"}}),
            ("content_block_delta", {"delta": {"type": "text_delta", "text": "확인해볼게!"}}),
            ("content_block_stop", {}),
            ("content_block_start", {"content_block": {"type": "tool_use", "id": tool_id, "name": name}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": args_json[:3]}}),
            ("content_block_delta", {"delta": {"type": "input_json_delta", "partial_json": args_json[3:]}}),
            ("content_block_stop", {}),
            ("message_delta", {"delta": {"stop_reason": "tool_use"}}),
            ("message_stop", {}),
        ]
    )


class _FakeStreamResponse:
    def __init__(self, lines: list[str], *, status_code: int = 200, text: str = ""):
        self.status_code = status_code
        self.text = text
        self._lines = lines
        self.closed = False

    def iter_lines(self, decode_unicode: bool = False):
        yield from self._lines

    def close(self) -> None:
        self.closed = True


def _make_client(**overrides) -> AnthropicLlmClient:
    kwargs = dict(
        provider="anthropic",
        model="claude-opus-4-8",
        api_key="fake-key",
        base_url="https://example.test/gmsapi/api.anthropic.com",
        max_retries=0,
        retry_backoff_seconds=0.0,
    )
    kwargs.update(overrides)
    return AnthropicLlmClient(**kwargs)


class _RecordingPost:
    """requests.post 대역 — 요청 바디/헤더를 기록하고 준비된 응답을 차례로 돌려준다."""

    def __init__(self, responses):
        self._responses = list(responses)
        self.calls: list[dict] = []

    def __call__(self, url, *, json=None, headers=None, stream=None, timeout=None):
        self.calls.append(
            {"url": url, "body": json, "headers": headers, "stream": stream, "timeout": timeout}
        )
        result = self._responses.pop(0)
        if isinstance(result, Exception):
            raise result
        return result


class _FakeToolHub:
    def __init__(self, result):
        self._result = result
        self.calls: list[tuple[str, dict]] = []

    def call(self, name, arguments=None):
        self.calls.append((name, arguments or {}))
        return self._result


# --- complete ---


def test_complete_returns_streamed_text(monkeypatch):
    post = _RecordingPost([_FakeStreamResponse(_text_stream("안녕! 잘 지냈어?"))])
    monkeypatch.setattr(client_module.requests, "post", post)

    assert _make_client().complete("안녕") == "안녕! 잘 지냈어?"


def test_complete_returns_none_without_key(monkeypatch):
    post = _RecordingPost([])
    monkeypatch.setattr(client_module.requests, "post", post)

    assert _make_client(api_key=None).complete("안녕") is None
    assert post.calls == []


def test_request_follows_gms_proxy_contract(monkeypatch):
    """스트리밍 필수 / temperature 미전송 / x-api-key 인증 — GMS 프록시 실측 제약."""
    post = _RecordingPost([_FakeStreamResponse(_text_stream("응"))])
    monkeypatch.setattr(client_module.requests, "post", post)

    _make_client().complete("안녕", temperature=0.9)

    call = post.calls[0]
    assert call["url"].endswith("/v1/messages")
    assert call["stream"] is True
    assert call["body"]["stream"] is True
    assert "temperature" not in call["body"]
    assert call["headers"]["x-api-key"] == "fake-key"
    assert call["headers"]["anthropic-version"]
    assert "Authorization" not in call["headers"]
    assert call["body"]["system"]  # system은 메시지가 아니라 톱레벨 파라미터


# --- chat_with_tools ---


def test_chat_with_tools_plain_answer(monkeypatch):
    post = _RecordingPost([_FakeStreamResponse(_text_stream("물은 아직 괜찮아!"))])
    monkeypatch.setattr(client_module.requests, "post", post)

    reply = _make_client().chat_with_tools(
        [ChatMessage(role="user", content="목말라?")], _FakeToolHub({})
    )
    assert reply == "물은 아직 괜찮아!"
    assert post.calls[0]["body"]["tools"] == anthropic_tool_definitions()


def test_chat_with_tools_runs_tool_round(monkeypatch):
    args_json = '{"limit": 5}'
    post = _RecordingPost(
        [
            _FakeStreamResponse(_tool_use_stream("tu_1", "get_recent_events", args_json)),
            _FakeStreamResponse(_text_stream("최근에 물을 받았어!")),
        ]
    )
    monkeypatch.setattr(client_module.requests, "post", post)
    hub = _FakeToolHub({"events": ["급수"]})

    reply = _make_client().chat_with_tools(
        [ChatMessage(role="user", content="최근 소식은?")], hub
    )

    assert reply == "최근에 물을 받았어!"
    # 쪼개진 input_json_delta 조각이 합쳐져 파싱됐는지
    assert hub.calls == [("get_recent_events", {"limit": 5})]

    # 2번째 요청에 assistant tool_use + user tool_result가 이어졌는지
    followup = post.calls[1]["body"]["messages"]
    assert followup[-2]["role"] == "assistant"
    assert any(b.get("type") == "tool_use" and b["id"] == "tu_1" for b in followup[-2]["content"])
    result_block = followup[-1]["content"][0]
    assert followup[-1]["role"] == "user"
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "tu_1"
    assert "급수" in result_block["content"]


def test_chat_with_tools_gives_up_after_max_rounds(monkeypatch):
    stream = lambda: _FakeStreamResponse(_tool_use_stream("tu_x", "get_plant_status", "{}"))  # noqa: E731
    post = _RecordingPost([stream(), stream(), stream()])
    monkeypatch.setattr(client_module.requests, "post", post)

    reply = _make_client().chat_with_tools(
        [ChatMessage(role="user", content="상태는?")],
        _FakeToolHub({}),
        max_tool_rounds=2,
    )
    assert "다시 물어봐" in reply
    assert len(post.calls) == 3


def test_chat_with_tools_raises_when_unavailable():
    with pytest.raises(RuntimeError):
        _make_client(api_key=None).chat_with_tools(
            [ChatMessage(role="user", content="안녕")], _FakeToolHub({})
        )


# --- 오류 처리 ---


def test_retries_http_500_then_succeeds(monkeypatch):
    post = _RecordingPost(
        [
            _FakeStreamResponse([], status_code=500, text="oops"),
            _FakeStreamResponse(_text_stream("복구됐어")),
        ]
    )
    monkeypatch.setattr(client_module.requests, "post", post)
    monkeypatch.setattr(client_module.time, "sleep", lambda *_: None)

    assert _make_client(max_retries=1).complete("안녕") == "복구됐어"
    assert len(post.calls) == 2


def test_auth_error_is_not_retried(monkeypatch):
    post = _RecordingPost([_FakeStreamResponse([], status_code=401, text="bad key")])
    monkeypatch.setattr(client_module.requests, "post", post)

    with pytest.raises(LlmAuthError):
        _make_client(max_retries=2).complete("안녕")
    assert len(post.calls) == 1


def test_rate_limit_after_retries(monkeypatch):
    post = _RecordingPost([_FakeStreamResponse([], status_code=429, text="slow down")])
    monkeypatch.setattr(client_module.requests, "post", post)

    with pytest.raises(LlmRateLimitError):
        _make_client().complete("안녕")


def test_http_error_maps_to_api_error(monkeypatch):
    post = _RecordingPost([_FakeStreamResponse([], status_code=400, text="temperature not allowed")])
    monkeypatch.setattr(client_module.requests, "post", post)

    with pytest.raises(LlmApiError) as excinfo:
        _make_client().complete("안녕")
    assert excinfo.value.status == 400


def test_timeout_maps_to_timeout_error(monkeypatch):
    post = _RecordingPost([requests.exceptions.ConnectTimeout("boom")])
    monkeypatch.setattr(client_module.requests, "post", post)

    with pytest.raises(LlmTimeoutError):
        _make_client().complete("안녕")


def test_connection_error_maps_to_connection_error(monkeypatch):
    post = _RecordingPost([requests.exceptions.ConnectionError("refused")])
    monkeypatch.setattr(client_module.requests, "post", post)

    with pytest.raises(LlmConnectionError):
        _make_client().complete("안녕")


def test_sse_error_event_raises_api_error(monkeypatch):
    lines = _sse([("error", {"error": {"type": "overloaded_error", "message": "과부하"}})])
    post = _RecordingPost([_FakeStreamResponse(lines)])
    monkeypatch.setattr(client_module.requests, "post", post)

    with pytest.raises(LlmApiError):
        _make_client().complete("안녕")


# --- 형식 변환 ---


def test_to_anthropic_messages_converts_tool_history():
    history = [
        ChatMessage(role="user", content="상태 알려줘"),
        ChatMessage(
            role="assistant",
            content="잠깐만!",
            tool_calls=[
                {
                    "id": "tu_1",
                    "function": {"name": "get_plant_status", "arguments": "{}"},
                },
                {
                    "id": "tu_2",
                    "function": {"name": "get_recent_events", "arguments": '{"limit": 3}'},
                },
            ],
        ),
        ChatMessage(role="tool", content='{"ok": 1}', tool_call_id="tu_1"),
        ChatMessage(role="tool", content='{"ok": 2}', tool_call_id="tu_2"),
        ChatMessage(role="assistant", content="다 괜찮아!"),
    ]
    converted = client_module._to_anthropic_messages(history)

    assert converted[0] == {"role": "user", "content": "상태 알려줘"}
    blocks = converted[1]["content"]
    assert blocks[0] == {"type": "text", "text": "잠깐만!"}
    assert blocks[1]["type"] == "tool_use"
    assert blocks[2]["input"] == {"limit": 3}
    # 연속된 tool 응답 2개가 user 메시지 하나의 tool_result 블록들로 합쳐진다
    assert converted[2]["role"] == "user"
    assert [b["tool_use_id"] for b in converted[2]["content"]] == ["tu_1", "tu_2"]
    assert converted[3] == {"role": "assistant", "content": "다 괜찮아!"}


def test_anthropic_tool_definitions_shape():
    defs = anthropic_tool_definitions()
    assert {d["name"] for d in defs} == {
        "get_plant_status",
        "get_recent_events",
        "get_sensor_data",
        "get_weather",
        "search_plant_knowledge",
        "get_news",
    }
    for d in defs:
        assert set(d) == {"name", "description", "input_schema"}
        assert d["input_schema"]["type"] == "object"


# --- 팩토리 ---


def test_create_llm_client_anthropic_defaults(monkeypatch):
    # 기존 OpenAI용 env가 남아 있어도 anthropic 설정을 오염시키면 안 된다
    monkeypatch.setenv("OPENAI_MODEL", "gpt-4.1-nano")
    monkeypatch.setenv("OPENAI_MAX_TOKENS", "200")
    monkeypatch.delenv("ANTHROPIC_MODEL", raising=False)
    monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)
    monkeypatch.delenv("ANTHROPIC_MAX_TOKENS", raising=False)

    client = create_llm_client(
        {"llm": {"provider": "anthropic", "api_key": "k", "max_tokens": 400}}
    )
    assert isinstance(client, AnthropicLlmClient)
    assert client.model == "claude-opus-4-8"
    assert client.base_url == client_module.GMS_ANTHROPIC_BASE_URL
    assert client.max_tokens == 400  # OPENAI_MAX_TOKENS(200)에 오염되지 않음


def test_create_llm_client_anthropic_env_override(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    client = create_llm_client({"llm": {"provider": "anthropic", "api_key": "k"}})
    assert client.model == "claude-sonnet-4-6"


def test_create_llm_client_gms_still_returns_openai_client(monkeypatch):
    monkeypatch.delenv("OPENAI_MODEL", raising=False)
    client = create_llm_client({"llm": {"provider": "gms", "api_key": "k"}})
    assert type(client) is LlmClient
    assert client.model == "gpt-4.1-nano"
