"""LLM 응답 파이프라인 전용 예외 계층.

모든 예외는 LlmError(RuntimeError)를 상속한다. RuntimeError를 상속하는 이유:
기존 코드(DialogueService.chat_once 등)가 `except RuntimeError`로 LLM 실패를
잡아 오프라인 폴백으로 넘어가는데, 그 동작을 깨지 않고 유형별 처리를
추가하기 위해서다.
"""

from __future__ import annotations


class LlmError(RuntimeError):
    """LLM 파이프라인에서 발생하는 모든 오류의 공통 부모."""


class InvalidQuestionError(LlmError):
    """사용자 입력이 검증을 통과하지 못했을 때.

    reason: 프로그램용 코드 (empty / too_long / whitespace_only /
    special_chars_only / unsupported_chars), message_ko: 사용자에게
    보여줄 한국어 안내.
    """

    def __init__(self, reason: str, message_ko: str) -> None:
        super().__init__(f"invalid question ({reason}): {message_ko}")
        self.reason = reason
        self.message_ko = message_ko


class LlmTimeoutError(LlmError):
    """요청이 timeout 안에 끝나지 않았을 때."""


class LlmRateLimitError(LlmError):
    """HTTP 429 — 재시도를 다 소진하고도 rate limit에 막혔을 때."""


class LlmAuthError(LlmError):
    """HTTP 401/403 — API 키가 없거나 잘못됐을 때 (재시도 무의미)."""


class LlmConnectionError(LlmError):
    """DNS/소켓 등 네트워크 계층 오류."""


class LlmApiError(LlmError):
    """그 외 OpenAI 호환 API가 돌려준 HTTP 오류 (4xx/5xx)."""

    def __init__(self, status: int, detail: str) -> None:
        super().__init__(f"LLM HTTP {status}: {detail}")
        self.status = status
        self.detail = detail


class LlmResponseParseError(LlmError):
    """200 OK인데 응답 바디가 JSON으로 파싱되지 않을 때 (GMS 게이트웨이 이슈)."""


class InvalidResponseError(LlmError):
    """LLM 응답이 내용 검증(길이/금칙어/프롬프트 위반)에 걸렸을 때."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"invalid LLM response: {reason}")
        self.reason = reason
