"""LLM 응답 파이프라인(검증→프롬프트→호출→응답 검증→후처리) 단위 테스트.

ROS/API 키 없이 동작한다. LLM 호출은 스텁 클라이언트로 대체한다.
"""

from __future__ import annotations

import socket
import urllib.error
from typing import Optional

import pytest

from potner_llm import client as client_module
from potner_llm.client import LlmClient
from potner_llm.config import ServiceSettings, create_service_settings
from potner_llm.exceptions import (
    InvalidQuestionError,
    LlmApiError,
    LlmRateLimitError,
    LlmResponseParseError,
    LlmTimeoutError,
)
from potner_llm.models import PlantProfile, SensorSnapshot
from potner_llm.postprocessor import postprocess
from potner_llm.prompt_builder import build_system_prompt
from potner_llm.service import PlantChatService
from potner_llm.validator import check_response, validate_question

SETTINGS = ServiceSettings()


# --- 스텁: LlmClient 대역 ---


class _StubClient:
    """complete()의 반환값 또는 예외를 마음대로 정할 수 있는 대역."""

    model = "stub-model"

    def __init__(self, reply: Optional[str] = None, error: Optional[Exception] = None):
        self._reply = reply
        self._error = error
        self.last_system: Optional[str] = None
        self.last_prompt: Optional[str] = None

    def complete(self, user_prompt: str, *, system: str, temperature: float = 0.7):
        self.last_prompt = user_prompt
        self.last_system = system
        if self._error is not None:
            raise self._error
        return self._reply


def _service(reply=None, error=None, **kwargs) -> PlantChatService:
    return PlantChatService(client=_StubClient(reply=reply, error=error), **kwargs)


# --- 1. 입력 검증 ---


def test_validate_question_strips_and_passes():
    assert validate_question("  물 줘야 해?  ", SETTINGS) == "물 줘야 해?"


@pytest.mark.parametrize(
    "text,reason",
    [
        (None, "empty"),
        ("", "empty"),
        ("   \n\t ", "whitespace_only"),
        ("가" * 301, "too_long"),
        ("!!!???...", "special_chars_only"),
        ("Привет как дела", "unsupported_chars"),
    ],
)
def test_validate_question_rejects(text, reason):
    with pytest.raises(InvalidQuestionError) as exc_info:
        validate_question(text, SETTINGS)
    assert exc_info.value.reason == reason
    assert exc_info.value.message_ko  # 사용자 안내 문구가 있어야 한다


# --- 4. 시스템 프롬프트 ---


def test_system_prompt_embeds_profile_sensors_and_rules():
    profile = PlantProfile(name="토토", species="몬스테라", last_watered_at="2026-07-28T09:00")
    sensors = SensorSnapshot(soil=42.0, temp=26.1, humidity=55.0, light=800.0, co2=650.0)
    prompt = build_system_prompt(profile, sensors, SETTINGS)
    assert "토토" in prompt and "몬스테라" in prompt
    assert "42%" in prompt and "26.1°C" in prompt
    assert "2026-07-28T09:00" in prompt
    assert "한국어" in prompt and "120자" in prompt and "3문장" in prompt
    assert "1인칭" in prompt and "모른다고" in prompt


def test_system_prompt_handles_missing_sensors():
    prompt = build_system_prompt(PlantProfile(), SensorSnapshot(), SETTINGS)
    assert "센서 데이터 없음" in prompt


# --- 6. 응답 검증 ---


@pytest.mark.parametrize(
    "text,reason",
    [
        (None, "empty"),
        ("", "empty"),
        ("   ", "empty"),
        ("나 " * 400, "too_long"),
        ("아 씨발 목말라", "banned_word"),
        ("죄송하지만 저는 AI 언어 모델이라서요.", "prompt_leak"),
        ("I am just a happy plant!", "not_korean"),
    ],
)
def test_check_response_rejects(text, reason):
    assert check_response(text, SETTINGS) == reason


def test_check_response_accepts_normal_korean():
    assert check_response("오늘은 물을 조금만 줘도 괜찮아!", SETTINGS) is None


# --- 7. 후처리 ---


def test_postprocess_collapses_whitespace_and_newlines():
    out = postprocess("안녕!  \n\n\n나는   초록이야.\r\n반가워.", SETTINGS)
    assert out == "안녕! 나는 초록이야. 반가워."


def test_postprocess_limits_sentences():
    out = postprocess("하나야. 둘이야. 셋이야. 넷이야. 다섯이야.", SETTINGS)
    assert out == "하나야. 둘이야. 셋이야."


def test_postprocess_truncates_long_text_at_sentence_boundary():
    text = ("목이 말라서 물을 조금 마시고 싶어. " * 10).strip()
    out = postprocess(text, SETTINGS)
    assert len(out) <= SETTINGS.max_response_length


def test_postprocess_strips_emoji_and_markdown():
    out = postprocess("**오늘은** 기분이 좋아! 🌱😊 `물` 고마워~", SETTINGS)
    assert "🌱" not in out and "*" not in out and "`" not in out
    assert "오늘은 기분이 좋아!" in out


# --- 서비스 전체 흐름 (스펙 12번 시나리오) ---


def test_answer_normal_question():
    service = _service(
        reply="오늘은 물을 조금만 주면 좋겠어. 흙이 아직 촉촉하거든.",
        profile_provider=lambda: PlantProfile(name="토토"),
        sensor_provider=lambda: SensorSnapshot(soil=42.0, temp=26.1),
    )
    result = service.answer("물 줘야 해?")
    assert result["success"] is True
    assert "물" in result["message"]
    assert result["plant_name"] == "토토"
    assert result["sensor_summary"]["soil"] == 42.0
    assert result["sensor_summary"]["temp"] == 26.1
    assert result["timestamp"]
    assert "fallback" not in result


def test_answer_passes_question_and_dynamic_prompt_to_llm():
    stub = _StubClient(reply="응, 나 잘 지내고 있어.")
    service = PlantChatService(
        client=stub, profile_provider=lambda: PlantProfile(name="토토")
    )
    service.answer("잘 지내?")
    assert stub.last_prompt == "잘 지내?"
    assert "토토" in stub.last_system


def test_answer_empty_question_returns_error():
    result = _service(reply="무관").answer("")
    assert result["success"] is False
    assert result["error_code"] == "empty"
    assert result["message"]  # 친절한 안내 문구


def test_answer_too_long_question_returns_error():
    result = _service(reply="무관").answer("가" * 301)
    assert result["success"] is False
    assert result["error_code"] == "too_long"


def test_answer_api_failure_returns_friendly_fallback():
    result = _service(error=LlmApiError(500, "boom")).answer("잘 지내?")
    assert result["success"] is False
    assert result["error_code"] == "api_error"
    assert result["fallback"] is True
    assert "boom" not in result["message"]  # 내부 오류를 노출하지 않는다


def test_answer_timeout_returns_friendly_fallback():
    result = _service(error=LlmTimeoutError("timeout")).answer("잘 지내?")
    assert result["success"] is False
    assert result["error_code"] == "timeout"
    assert result["message"]


def test_answer_rate_limit_returns_friendly_fallback():
    result = _service(error=LlmRateLimitError("429")).answer("잘 지내?")
    assert result["error_code"] == "rate_limit"


def test_answer_json_parse_error_returns_fallback():
    result = _service(error=LlmResponseParseError("bad json")).answer("잘 지내?")
    assert result["success"] is False
    assert result["error_code"] == "parse_error"
    assert result["message"] == "지금은 대답하기 어려워요."


def test_answer_empty_llm_reply_returns_default_message():
    result = _service(reply="").answer("잘 지내?")
    assert result["message"] == "지금은 대답하기 어려워요."
    assert result["fallback"] is True
    assert result["error_code"] == "invalid_response:empty"


def test_answer_banned_word_reply_returns_default_message():
    result = _service(reply="씨발 목말라 죽겠네").answer("잘 지내?")
    assert result["message"] == "지금은 대답하기 어려워요."
    assert result["error_code"] == "invalid_response:banned_word"


def test_answer_uses_default_sensors_when_provider_returns_none():
    result = _service(reply="나는 잘 지내고 있어!", sensor_provider=lambda: None).answer("잘 지내?")
    assert result["sensor_summary"]["soil"] == 50.0  # config 기본값
    assert result["sensor_summary"]["temp"] == 24.0


def test_answer_survives_provider_exceptions():
    def broken_provider():
        raise ConnectionError("DB down")

    result = _service(
        reply="나는 잘 지내고 있어!",
        profile_provider=broken_provider,
        sensor_provider=broken_provider,
    ).answer("잘 지내?")
    assert result["success"] is True  # 기본 프로필/센서로 계속 진행


# --- 사실성 검증 배선: 통과한 답변만 사용자에게 전달된다 ---


def test_answer_factcheck_rejects_reply_contradicting_sensors():
    # 센서는 26.1°C인데 34도라고 주장 — 재생성해도 같은 답(스텁)이라 최종 폴백
    result = _service(
        reply="지금 34도라서 너무 더워.",
        sensor_provider=lambda: SensorSnapshot(temp=26.1),
    ).answer("더워?")
    assert result["fallback"] is True
    assert result["error_code"].startswith("fact_check:")
    assert result["message"] == "지금은 대답하기 어려워요."
    assert "34도" not in result["message"]


def test_answer_factcheck_retries_then_delivers_corrected_reply():
    class _RetryStub(_StubClient):
        def __init__(self):
            super().__init__()
            self.calls = 0

        def complete(self, user_prompt: str, *, system: str, temperature: float = 0.7):
            self.calls += 1
            if self.calls == 1:
                return "흙 수분이 80%야."  # 센서(42%)와 상충 → 재생성 유도
            return "흙 수분이 42%라서 딱 좋아."

    stub = _RetryStub()
    service = PlantChatService(
        client=stub, sensor_provider=lambda: SensorSnapshot(soil=42.0)
    )
    result = service.answer("흙 어때?")
    assert stub.calls == 2
    assert result["success"] is True
    assert "42%" in result["message"]
    assert "fallback" not in result


# --- 클라이언트: timeout이 LlmTimeoutError로 매핑되는지 ---


def test_client_raises_timeout_error_after_retries(monkeypatch):
    def fake_urlopen(req, timeout=60):
        raise urllib.error.URLError(socket.timeout("timed out"))

    monkeypatch.setattr(client_module.urllib.request, "urlopen", fake_urlopen)
    client = LlmClient(
        provider="gms",
        model="gpt-4.1-nano",
        api_key="fake-key",
        base_url="https://example.test",
        max_retries=1,
        retry_backoff_seconds=0.01,
        request_timeout_seconds=0.5,
    )
    with pytest.raises(LlmTimeoutError):
        client.complete("hi")


def test_create_service_settings_reads_env(monkeypatch):
    monkeypatch.setenv("LLM_MAX_RESPONSE_LENGTH", "80")
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0.3")
    settings = create_service_settings({})
    assert settings.max_response_length == 80
    assert settings.temperature == 0.3
