"""potner_llm 순수 로직 단위 테스트 (ROS / API 키 불필요)."""

from __future__ import annotations

import io
import urllib.error

import pytest

from potner_llm import client as client_module
from potner_llm import conversation_backend as conversation_backend_module
from potner_llm.client import LlmClient, create_llm_client, describe_llm
from potner_llm.conversation_backend import (
    FileConversationBackend,
    HttpConversationBackend,
    create_conversation_backend,
)
from potner_llm.dialogue import DialogueService
from potner_llm.events import EventStore
from potner_llm.prompts import SYSTEM_PLANT, briefing_user_prompt
from potner_llm.status import MetricLevel, PlantStatus
from potner_llm.templates import render_briefing
from potner_llm.tools import ToolHub


def _sample_status(*, needs_attention: bool = False) -> PlantStatus:
    level = "low" if needs_attention else "normal"
    label = "건조" if needs_attention else "적정"
    metric = MetricLevel(name="soil", value=20.0 if needs_attention else 50.0, level=level, label_ko=label)
    ok = MetricLevel(name="ok", value=25.0, level="normal", label_ko="적정")
    return PlantStatus(
        timestamp="2026-07-28T00:00:00",
        soil=metric,
        temperature=ok,
        humidity=ok,
        light=ok,
        summary_ko="토양이 건조해요" if needs_attention else "전반적으로 양호해요",
        needs_attention=needs_attention,
    )


def test_system_prompt_is_korean_first_person():
    assert "식물" in SYSTEM_PLANT
    assert "1인칭" in SYSTEM_PLANT


def test_briefing_prompt_embeds_status():
    prompt = briefing_user_prompt({"요약": "양호"})
    assert "양호" in prompt
    assert "브리핑" in prompt


def test_template_briefing_fallback():
    text = render_briefing(_sample_status(needs_attention=False))
    assert "괜찮아" in text


def test_create_llm_client_mock_is_unavailable():
    client = create_llm_client({"llm": {"provider": "mock", "enabled": True}})
    assert client.provider == "mock"
    assert client.available() is False
    assert "mock" in describe_llm(client, {"llm": {"provider": "mock"}})


def test_tool_hub_get_status(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    store.append({"message": "물 부족", "timestamp": "2026-07-28T00:00:00"})
    hub = ToolHub(get_status=_sample_status, event_store=store)
    status = hub.call("get_plant_status")
    assert status["주의필요"] is False
    events = hub.call("get_recent_events", {"limit": 1})
    assert events[0]["message"] == "물 부족"


def test_dialogue_offline_chat_fallback(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "mock"}},
        get_status=_sample_status,
        event_store=store,
    )
    reply = service.chat_once("지금 어때?")
    assert "로컬 모드" in reply
    assert "양호" in reply or "적정" in reply


class _FakeResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        import json

        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


class _RawResponse:
    """응답 바이트를 그대로 흉내낼 때 쓰는 가짜 응답 (JSON이 아닌 것도 넣을 수 있음)."""

    def __init__(self, raw: bytes):
        self._raw = raw

    def read(self) -> bytes:
        return self._raw

    def __enter__(self):
        return self

    def __exit__(self, *exc_info):
        return False


def _http_error(status: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError("https://example.test", status, "err", {}, io.BytesIO(b"detail"))


def _make_client(**overrides) -> LlmClient:
    kwargs = dict(
        provider="gms",
        model="gpt-4.1-nano",
        api_key="fake-key",
        base_url="https://example.test",
        max_retries=2,
        retry_backoff_seconds=0.01,
    )
    kwargs.update(overrides)
    return LlmClient(**kwargs)


def test_post_chat_retries_transient_errors_then_succeeds(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=60):
        calls["n"] += 1
        if calls["n"] < 3:
            raise _http_error(503)
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)

    llm = _make_client()
    data = llm._post_chat([{"role": "user", "content": "hi"}], tools=None, temperature=0.5)

    assert data["choices"][0]["message"]["content"] == "ok"
    assert calls["n"] == 3


def test_post_chat_gives_up_after_max_retries(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=60):
        calls["n"] += 1
        raise _http_error(500)

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)

    llm = _make_client(max_retries=1)
    with pytest.raises(RuntimeError):
        llm._post_chat([{"role": "user", "content": "hi"}], tools=None, temperature=0.5)

    assert calls["n"] == 2  # 최초 시도 + 재시도 1회


def test_post_chat_does_not_retry_auth_errors(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=60):
        calls["n"] += 1
        raise _http_error(401)

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)

    llm = _make_client(max_retries=2)
    with pytest.raises(RuntimeError):
        llm._post_chat([{"role": "user", "content": "hi"}], tools=None, temperature=0.5)

    assert calls["n"] == 1  # 인증 오류는 재시도하지 않음


def test_post_chat_retries_malformed_json_then_succeeds(monkeypatch):
    """200 OK인데 응답 바디에 잉여 데이터가 섞여 오는 경우(실제로 겪은 사례) 재시도한다."""
    calls = {"n": 0}

    def fake_urlopen(req, timeout=60):
        calls["n"] += 1
        if calls["n"] < 2:
            return _RawResponse(b'{"choices": [{"message": {"content": "oops"}}]}extra-garbage')
        return _FakeResponse({"choices": [{"message": {"content": "ok"}}]})

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)

    llm = _make_client()
    data = llm._post_chat([{"role": "user", "content": "hi"}], tools=None, temperature=0.5)

    assert data["choices"][0]["message"]["content"] == "ok"
    assert calls["n"] == 2


def test_post_chat_gives_up_on_persistent_malformed_json(monkeypatch):
    calls = {"n": 0}

    def fake_urlopen(req, timeout=60):
        calls["n"] += 1
        return _RawResponse(b'{"a": 1}extra-garbage')

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(client_module.time, "sleep", lambda _seconds: None)

    llm = _make_client(max_retries=1)
    with pytest.raises(RuntimeError):
        llm._post_chat([{"role": "user", "content": "hi"}], tools=None, temperature=0.5)

    assert calls["n"] == 2  # 최초 시도 + 재시도 1회


def test_dialogue_chat_once_keeps_multiturn_history(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "gms"}},
        get_status=_sample_status,
        event_store=store,
    )
    service.llm.api_key = "fake-key"

    seen_histories = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen_histories.append(list(history))
        return f"reply-{len(seen_histories)}"

    service.llm.chat_with_tools = fake_chat_with_tools

    first = service.chat_once("안녕")
    second = service.chat_once("두번째 질문")

    assert first == "reply-1"
    assert second == "reply-2"
    assert [m.content for m in seen_histories[0]] == ["안녕"]
    assert [m.content for m in seen_histories[1]] == ["안녕", "reply-1", "두번째 질문"]


def test_dialogue_reset_history_clears_context(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "gms"}},
        get_status=_sample_status,
        event_store=store,
    )
    service.llm.api_key = "fake-key"

    seen_histories = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen_histories.append(list(history))
        return "ok"

    service.llm.chat_with_tools = fake_chat_with_tools

    service.chat_once("첫 질문")
    service.reset_history()
    service.chat_once("리셋 후 질문")

    assert [m.content for m in seen_histories[-1]] == ["리셋 후 질문"]


def test_dialogue_history_is_capped_by_max_history_turns(tmp_path):
    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "gms"}},
        get_status=_sample_status,
        event_store=store,
        max_history_turns=1,
    )
    service.llm.api_key = "fake-key"

    seen_histories = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen_histories.append(list(history))
        return f"reply-{len(seen_histories)}"

    service.llm.chat_with_tools = fake_chat_with_tools

    service.chat_once("첫번째")
    service.chat_once("두번째")
    service.chat_once("세번째")

    assert [m.content for m in seen_histories[-1]] == ["두번째", "reply-2", "세번째"]


def test_file_conversation_backend_round_trip(tmp_path):
    backend = FileConversationBackend(tmp_path / "conversations.json")

    assert backend.load("potner-01") == []

    messages = [{"role": "user", "content": "안녕"}, {"role": "assistant", "content": "반가워"}]
    backend.save("potner-01", messages)

    assert backend.load("potner-01") == messages
    assert backend.load("other-session") == []


def test_create_conversation_backend_defaults_to_file(tmp_path):
    backend = create_conversation_backend({}, default_dir=tmp_path)
    assert isinstance(backend, FileConversationBackend)
    assert backend.path == tmp_path / "conversation_history.json"


def test_create_conversation_backend_http_requires_base_url():
    with pytest.raises(ValueError):
        create_conversation_backend({"conversation": {"backend": "http"}})


def test_create_conversation_backend_http_uses_base_url():
    backend = create_conversation_backend(
        {"conversation": {"backend": "http", "base_url": "https://example.test/api"}}
    )
    assert isinstance(backend, HttpConversationBackend)
    assert backend.base_url == "https://example.test/api"


def test_http_conversation_backend_load_returns_empty_on_404(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise _http_error(404)

    monkeypatch.setattr(conversation_backend_module.urllib.request, "urlopen", fake_urlopen)

    backend = HttpConversationBackend("https://example.test/api")
    assert backend.load("potner-01") == []


def test_http_conversation_backend_load_returns_empty_on_connection_error(monkeypatch):
    def fake_urlopen(req, timeout=10):
        raise conversation_backend_module.urllib.error.URLError("no route to host")

    monkeypatch.setattr(conversation_backend_module.urllib.request, "urlopen", fake_urlopen)

    backend = HttpConversationBackend("https://example.test/api")
    assert backend.load("potner-01") == []


def test_http_conversation_backend_save_sends_expected_request(monkeypatch):
    captured = {}

    def fake_urlopen(req, timeout=10):
        captured["url"] = req.full_url
        captured["method"] = req.get_method()
        captured["body"] = req.data
        return _FakeResponse({})

    monkeypatch.setattr(conversation_backend_module.urllib.request, "urlopen", fake_urlopen)

    backend = HttpConversationBackend("https://example.test/api")
    messages = [{"role": "user", "content": "안녕"}]
    backend.save("potner-01", messages)

    import json

    assert captured["url"] == "https://example.test/api/conversations/potner-01"
    assert captured["method"] == "PUT"
    assert json.loads(captured["body"]) == {"messages": messages}


def test_dialogue_restores_history_from_backend(tmp_path):
    backend = FileConversationBackend(tmp_path / "conversations.json")
    backend.save(
        "potner-01",
        [{"role": "user", "content": "예전에 물어봤어"}, {"role": "assistant", "content": "기억해"}],
    )

    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "mock"}},
        get_status=_sample_status,
        event_store=store,
        conversation_backend=backend,
        session_id="potner-01",
    )

    assert [m.content for m in service.history] == ["예전에 물어봤어", "기억해"]


def test_dialogue_end_conversation_saves_and_next_session_continues(tmp_path):
    backend = FileConversationBackend(tmp_path / "conversations.json")
    store = EventStore(tmp_path / "events.jsonl")

    def make_service():
        service = DialogueService(
            {"llm": {"provider": "gms"}},
            get_status=_sample_status,
            event_store=store,
            conversation_backend=backend,
            session_id="potner-01",
        )
        service.llm.api_key = "fake-key"
        return service

    seen_histories = []

    def fake_chat_with_tools(history, tools, **kwargs):
        seen_histories.append(list(history))
        return f"reply-{len(seen_histories)}"

    first_session = make_service()
    first_session.llm.chat_with_tools = fake_chat_with_tools
    first_session.chat_once("첫 대화")
    first_session.end_conversation()

    assert first_session.history == []
    saved = backend.load("potner-01")
    assert [m["content"] for m in saved] == ["첫 대화", "reply-1"]

    second_session = make_service()
    assert [m.content for m in second_session.history] == ["첫 대화", "reply-1"]

    second_session.llm.chat_with_tools = fake_chat_with_tools
    second_session.chat_once("두번째 세션 질문")

    assert [m.content for m in seen_histories[-1]] == ["첫 대화", "reply-1", "두번째 세션 질문"]


def test_dialogue_end_conversation_without_new_turns_does_not_wipe_saved_history(tmp_path):
    backend = FileConversationBackend(tmp_path / "conversations.json")
    backend.save(
        "potner-01",
        [{"role": "user", "content": "예전 대화"}, {"role": "assistant", "content": "기억함"}],
    )

    store = EventStore(tmp_path / "events.jsonl")
    service = DialogueService(
        {"llm": {"provider": "mock"}},
        get_status=_sample_status,
        event_store=store,
        conversation_backend=backend,
        session_id="potner-01",
    )

    # 아무 대화도 하지 않고 바로 종료 - 저장된 이전 대화가 지워지면 안 된다.
    service.end_conversation()

    saved = backend.load("potner-01")
    assert [m["content"] for m in saved] == ["예전 대화", "기억함"]
