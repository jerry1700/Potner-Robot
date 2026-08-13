"""응답 파이프라인 설정·상수 모음.

환경변수 > config dict(llm.yaml) > 기본값 순서로 읽는다.
LlmClient 자체 설정(create_llm_client)과 별개로, 검증/후처리 규칙처럼
"서비스 정책"에 해당하는 값들을 여기에 모아 둔다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any

# --- 입력 검증 ---
MAX_QUESTION_LENGTH = 300

# --- 응답 검증 ---
MAX_RESPONSE_LENGTH = 120  # 시스템 프롬프트의 "120자 이내"와 같은 값
MAX_RESPONSE_SENTENCES = 3
# 폴백 응답: 응답 검증 실패 시 사용자에게 돌려줄 기본 문장
FALLBACK_MESSAGE = "지금은 대답하기 어려워요."

# 금칙어: 욕설·위험 조언 키워드. LLM 응답에 하나라도 포함되면 검증 실패.
BANNED_WORDS: tuple[str, ...] = (
    "씨발",
    "시발",
    "병신",
    "개새끼",
    "존나",
    "미친놈",
    "죽어버려",
    "농약을 마시",
    "표백제",
    "살충제를 먹",
)

# 프롬프트 위반 탐지: 응답에 이런 문구가 보이면 시스템 프롬프트가 새어 나온 것
PROMPT_LEAK_MARKERS: tuple[str, ...] = (
    "system prompt",
    "시스템 프롬프트",
    "As an AI",
    "as an AI language model",
    "저는 AI 언어 모델",
    "저는 인공지능 모델",
)

# --- 센서 기본값: 데이터가 없을 때 프롬프트에 넣을 값 ---
DEFAULT_SENSOR_VALUES: dict[str, Any] = {
    "soil": 50.0,       # %
    "temp": 24.0,       # °C
    "humidity": 50.0,   # %
    "light": 300.0,     # lux
    "co2": 600.0,       # ppm
}


@dataclass(frozen=True)
class ServiceSettings:
    """LLM 응답 서비스의 튜닝 가능한 정책 값들."""

    max_question_length: int = MAX_QUESTION_LENGTH
    max_response_length: int = MAX_RESPONSE_LENGTH
    max_response_sentences: int = MAX_RESPONSE_SENTENCES
    fallback_message: str = FALLBACK_MESSAGE
    temperature: float = 0.7
    strip_emoji: bool = True
    banned_words: tuple[str, ...] = BANNED_WORDS
    prompt_leak_markers: tuple[str, ...] = PROMPT_LEAK_MARKERS
    default_sensor_values: dict[str, Any] = field(
        default_factory=lambda: dict(DEFAULT_SENSOR_VALUES)
    )


def create_service_settings(config: dict[str, Any] | None = None) -> ServiceSettings:
    """환경변수·config dict에서 ServiceSettings를 만든다.

    config 예시 (llm.yaml)::

        llm:
          temperature: 0.7
          response:
            max_length: 120
            max_sentences: 3
            strip_emoji: true
    """
    llm = (config or {}).get("llm", {}) or {}
    response = llm.get("response", {}) or {}

    def _env_or(name: str, fallback: Any) -> Any:
        return os.environ.get(name) or fallback

    return ServiceSettings(
        max_question_length=int(
            _env_or("LLM_MAX_QUESTION_LENGTH", response.get("max_question_length", MAX_QUESTION_LENGTH))
        ),
        max_response_length=int(
            _env_or("LLM_MAX_RESPONSE_LENGTH", response.get("max_length", MAX_RESPONSE_LENGTH))
        ),
        max_response_sentences=int(
            _env_or("LLM_MAX_RESPONSE_SENTENCES", response.get("max_sentences", MAX_RESPONSE_SENTENCES))
        ),
        fallback_message=str(response.get("fallback_message", FALLBACK_MESSAGE)),
        temperature=float(_env_or("OPENAI_TEMPERATURE", llm.get("temperature", 0.7))),
        strip_emoji=bool(response.get("strip_emoji", True)),
    )
