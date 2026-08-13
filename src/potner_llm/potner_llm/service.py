"""LLM 응답 파이프라인 오케스트레이션.

흐름: 입력 검증 → 식물 정보 조회 → 센서 조회 → 시스템 프롬프트 생성
      → LLM 호출 → 응답 검증 → 후처리 → JSON(dict) 반환

FastAPI/Flask 핸들러나 ROS 노드에서 PlantChatService.answer()만 부르면
바로 응답 JSON으로 쓸 수 있는 dict가 나온다.
"""

from __future__ import annotations

import logging
import time
from datetime import datetime
from typing import Any, Callable, Optional

from .client import LlmClient, create_llm_client
from .config import ServiceSettings, create_service_settings
from .exceptions import (
    InvalidQuestionError,
    LlmAuthError,
    LlmConnectionError,
    LlmError,
    LlmRateLimitError,
    LlmResponseParseError,
    LlmTimeoutError,
)
from .factcheck import ACTION_RETRY, DEFAULT_POLICY, next_action, verify_response
from .models import ChatResult, PlantProfile, SensorSnapshot
from .postprocessor import postprocess
from .prompt_builder import build_system_prompt
from .validator import check_response, validate_question

logger = logging.getLogger(__name__)

ProfileProvider = Callable[[], PlantProfile]
SensorProvider = Callable[[], Optional[SensorSnapshot]]

# 예외 유형별 사용자 안내 문구 (친절한 메시지, 내부 사정은 숨긴다)
_ERROR_MESSAGES: list[tuple[type, str, str]] = [
    (LlmTimeoutError, "timeout", "생각이 너무 길어졌어요. 잠시 뒤에 다시 물어봐 줄래요?"),
    (LlmRateLimitError, "rate_limit", "지금 질문이 몰려서 바빠요. 조금 있다가 다시 물어봐 주세요."),
    (LlmAuthError, "auth_error", "지금은 대답하기 어려워요."),
    (LlmConnectionError, "network_error", "바깥세상과 연결이 잠깐 끊겼어요. 다시 시도해 주세요."),
    (LlmResponseParseError, "parse_error", "지금은 대답하기 어려워요."),
]


class PlantChatService:
    """사용자 질문 하나를 받아 검증·조회·호출·후처리를 거쳐 응답을 만든다.

    profile_provider / sensor_provider를 주입하면 DB·ROS 토픽 등 어떤
    소스든 붙일 수 있고, 생략하면 Mock 기본값으로 동작한다 (개발/테스트용).
    """

    def __init__(
        self,
        config: dict[str, Any] | None = None,
        *,
        client: Optional[LlmClient] = None,
        settings: Optional[ServiceSettings] = None,
        profile_provider: Optional[ProfileProvider] = None,
        sensor_provider: Optional[SensorProvider] = None,
    ) -> None:
        config = config or {}
        self.settings = settings or create_service_settings(config)
        self.client = client or create_llm_client(config)
        self._get_profile = profile_provider or PlantProfile
        self._get_sensors: SensorProvider = sensor_provider or (lambda: None)

    def answer(self, question: Optional[str]) -> dict[str, Any]:
        """질문 하나에 대한 응답 JSON(dict)을 돌려준다. 예외를 던지지 않는다."""
        started = time.monotonic()
        timestamp = datetime.now().astimezone().isoformat(timespec="seconds")

        profile = self._safe_profile()
        raw_sensors = self._safe_sensors()
        # 사실성 검증은 기본값으로 채우기 전의 원시 스냅샷 기준 —
        # DEFAULT_SENSOR_VALUES로 지어낸 값과 맞춰보면 가짜 근거 검증이 된다.
        sensors = raw_sensors.with_defaults(self.settings.default_sensor_values)

        # 1) 입력 검증
        try:
            cleaned_question = validate_question(question, self.settings)
        except InvalidQuestionError as exc:
            logger.info(f"질문 검증 실패 ({exc.reason}): {question!r}")
            return self._result(
                success=False,
                message=exc.message_ko,
                profile=profile,
                sensors=sensors,
                timestamp=timestamp,
                error_code=exc.reason,
            ).to_dict()

        # 2~5) 프롬프트 생성 + LLM 호출
        system_prompt = build_system_prompt(profile, sensors, self.settings)
        logger.info(f"LLM 요청 시작 model={self.client.model} question={cleaned_question!r}")

        fact = None
        for attempt in range(DEFAULT_POLICY.max_retries + 1):
            try:
                raw_reply = self.client.complete(
                    cleaned_question,
                    system=system_prompt,
                    temperature=self.settings.temperature,
                )
            except LlmError as exc:
                return self._error_result(exc, profile, sensors, timestamp, started).to_dict()
            if raw_reply is None:
                break
            # 5.5) 사실성 검증 — 후처리(문장 절단) 전 원문 기준. 상충이면 재생성.
            fact = verify_response(raw_reply, raw_sensors, profile)
            if next_action(fact, attempt) != ACTION_RETRY:
                break

        elapsed_ms = (time.monotonic() - started) * 1000

        if raw_reply is None:
            # 클라이언트 비활성(키 없음/mock) — LLM 없이 기본 응답으로 처리
            logger.warning(f"LLM 사용 불가 상태 — 폴백 응답 반환 ({elapsed_ms:.0f}ms)")
            return self._result(
                success=False,
                message=self.settings.fallback_message,
                profile=profile,
                sensors=sensors,
                timestamp=timestamp,
                error_code="llm_unavailable",
                fallback=True,
            ).to_dict()

        # 6) 응답 검증
        invalid_reason = check_response(raw_reply, self.settings)
        if invalid_reason is not None:
            logger.warning(
                f"LLM 응답 검증 실패 ({invalid_reason}) — 폴백 응답 반환 ({elapsed_ms:.0f}ms) "
                f"reply[:80]={(raw_reply[:80] if raw_reply else raw_reply)!r}"
            )
            return self._result(
                success=True,
                message=self.settings.fallback_message,
                profile=profile,
                sensors=sensors,
                timestamp=timestamp,
                error_code=f"invalid_response:{invalid_reason}",
                fallback=True,
            ).to_dict()

        # 6.5) 사실성 검증 최종 판정 — 재생성까지 실패면 폴백 (무응답 금지 불변식 유지)
        if fact is not None and not fact.ok:
            logger.warning(
                f"사실성 검증 최종 실패 — 폴백 응답 반환 ({elapsed_ms:.0f}ms) "
                f"사유={', '.join(fact.issue_codes)}"
            )
            return self._result(
                success=True,
                message=self.settings.fallback_message,
                profile=profile,
                sensors=sensors,
                timestamp=timestamp,
                error_code=f"fact_check:{fact.issue_codes[0]}",
                fallback=True,
            ).to_dict()

        # 7) 후처리
        message = postprocess(raw_reply, self.settings)
        if not message:
            logger.warning("후처리 결과가 비어 있음 — 폴백 응답 반환")
            return self._result(
                success=True,
                message=self.settings.fallback_message,
                profile=profile,
                sensors=sensors,
                timestamp=timestamp,
                error_code="invalid_response:empty_after_postprocess",
                fallback=True,
            ).to_dict()

        logger.info(f"LLM 응답 완료 model={self.client.model} {elapsed_ms:.0f}ms len={len(message)}")
        return self._result(
            success=True,
            message=message,
            profile=profile,
            sensors=sensors,
            timestamp=timestamp,
        ).to_dict()

    # --- 내부 도우미 ---

    def _safe_profile(self) -> PlantProfile:
        try:
            return self._get_profile()
        except Exception:
            logger.exception("식물 프로필 조회 실패 — 기본 프로필 사용")
            return PlantProfile()

    def _safe_sensors(self) -> SensorSnapshot:
        try:
            return self._get_sensors() or SensorSnapshot()
        except Exception:
            logger.exception("센서 데이터 조회 실패 — 기본값 사용")
            return SensorSnapshot()

    def _error_result(
        self,
        exc: LlmError,
        profile: PlantProfile,
        sensors: SensorSnapshot,
        timestamp: str,
        started: float,
    ) -> ChatResult:
        elapsed_ms = (time.monotonic() - started) * 1000
        for exc_type, code, message_ko in _ERROR_MESSAGES:
            if isinstance(exc, exc_type):
                logger.error(f"LLM 호출 실패 ({code}, {elapsed_ms:.0f}ms): {exc}")
                return self._result(
                    success=False,
                    message=message_ko,
                    profile=profile,
                    sensors=sensors,
                    timestamp=timestamp,
                    error_code=code,
                    fallback=True,
                )
        logger.error(f"LLM 호출 실패 (api_error, {elapsed_ms:.0f}ms): {exc}")
        return self._result(
            success=False,
            message=self.settings.fallback_message,
            profile=profile,
            sensors=sensors,
            timestamp=timestamp,
            error_code="api_error",
            fallback=True,
        )

    def _result(
        self,
        *,
        success: bool,
        message: str,
        profile: PlantProfile,
        sensors: SensorSnapshot,
        timestamp: str,
        error_code: Optional[str] = None,
        fallback: bool = False,
    ) -> ChatResult:
        return ChatResult(
            success=success,
            message=message,
            plant_name=profile.name,
            timestamp=timestamp,
            sensor_summary=sensors.summary_dict(),
            error_code=error_code,
            fallback=fallback,
        )
